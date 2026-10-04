"""Safely reconcile a stopped OTA when the exact preflash source is unchanged.

This helper never flashes, retries, re-interviews, binds, or configures the target.
Strict/manual mode requires fresh source identity, a quiet observation window and
an exact OTA check proving the intended candidate is still available.

Supervised fast mode may defer that candidate check because the supervisor runs
a fresh exact OTA check immediately before any retry. Fast reconciliation still
requires exact source identity, fresh target GET, matching campaign/network
locks and a bounded quiet window with no active OTA.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import yaml

from bseed_ota_campaign import load_profile, network_lock_path
from bseed_network_campaign_lock import (
    read_lock,
    release_source_unchanged,
    update as update_network_lock,
)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def inventory_match(devices, profile):
    matches = [d for d in (devices or []) if d.get('ieee_address') == profile['ieee']]
    if len(matches) != 1:
        raise ValueError('Target IEEE absent or ambiguous')
    d = matches[0]
    interview_ok = d.get('interview_state') == 'SUCCESSFUL' or d.get('interview_completed') is True
    if (d.get('friendly_name'), d.get('manufacturer'), d.get('model_id'),
        d.get('type'), d.get('software_build_id')) != (
            profile['device'], profile['manufacturer'], profile['model'],
            profile['preflash_role'], profile['preflash_build']):
        raise ValueError('Fresh inventory no longer matches exact preflash identity')
    if not interview_ok:
        raise ValueError('Preflash source interview is not complete')
    return d


def reconcile(profile_path, confirmation, observe_seconds=45, verify_candidate=True):
    profile = load_profile(profile_path)
    if confirmation != profile['ieee']:
        raise ValueError('Confirm exact IEEE for source-unchanged reconciliation')
    work = Path(profile['workdir'])
    work_lock_path = work / 'ACTIVE_LOCK.json'
    if not work_lock_path.is_file():
        raise ValueError('Campaign lock missing')
    work_lock = json.loads(work_lock_path.read_text(encoding='utf8'))
    if (work_lock.get('device'), work_lock.get('ieee'), work_lock.get('sha256')) != (
            profile['device'], profile['ieee'], profile['sha256']):
        raise ValueError('Campaign lock identity/hash mismatch')
    if work_lock.get('phase') not in (
            'ota_running', 'update_timeout_or_unconfirmed', 'update_error',
            'source_unchanged_reconciled'):
        raise ValueError('Campaign phase is not eligible for source-unchanged reconciliation')
    token = work_lock.get('token')
    if not token:
        raise ValueError('Campaign token missing')

    network_path = network_lock_path(profile, required=True)
    network = read_lock(network_path)
    if network is None or network.get('token') != token:
        raise ValueError('Shared network lock missing or token mismatch')
    if (network.get('device'), network.get('ieee'), network.get('image_sha256')) != (
            profile['device'], profile['ieee'], profile['sha256']):
        raise ValueError('Shared network lock identity/hash mismatch')

    config = yaml.safe_load(Path(profile['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    ready = threading.Event()
    wake = threading.Event()
    fresh = threading.Event()
    checked = threading.Event()
    state = dict(devices=None, info=None, bridge=None, target=None,
                 update_seen=False, check=None)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='bseed-source-reconcile-' + uuid.uuid4().hex)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))

    def on_connect(c, _u, _f, reason, _props):
        if reason.is_failure:
            return
        c.subscribe([
            (base + '/bridge/devices', 1),
            (base + '/bridge/info', 1),
            (base + '/bridge/state', 1),
            (base + '/' + profile['device'], 1),
            (base + '/bridge/response/device/ota_update/check', 1),
        ])
        ready.set()

    def on_message(_c, _u, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        topic = message.topic
        if topic == base + '/bridge/devices' and isinstance(data, list):
            state['devices'] = data
        elif topic == base + '/bridge/info' and isinstance(data, dict):
            state['info'] = data
        elif topic == base + '/bridge/state':
            state['bridge'] = data.get('state') if isinstance(data, dict) else data
        elif topic == base + '/' + profile['device'] and isinstance(data, dict):
            update = data.get('update')
            if isinstance(update, dict) and update.get('state') == 'updating':
                state['update_seen'] = True
            if not message.retain:
                state['target'] = data
                fresh.set()
        elif topic == base + '/bridge/response/device/ota_update/check' and isinstance(data, dict):
            transaction = data.get('transaction')
            if transaction and transaction == state.get('check_token'):
                state['check'] = data
                checked.set()
        wake.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(profile['broker'], 1883, 10)
    client.loop_start()
    try:
        if not ready.wait(10):
            raise TimeoutError('MQTT subscribe failed')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not all(
                state[k] is not None for k in ('devices', 'info', 'bridge')):
            wake.wait(0.5); wake.clear()
        if state['bridge'] != 'online':
            raise ValueError('Zigbee2MQTT bridge not online')
        if not isinstance(state['info'], dict):
            raise ValueError('Bridge info missing')
        coordinator = (state['info'].get('coordinator') or {}).get('ieee_address')
        if coordinator != profile.get('network_id'):
            raise ValueError('Live coordinator differs from campaign network_id')
        inventory_match(state['devices'], profile)

        state['target'] = None
        fresh.clear()
        client.publish(base + '/' + profile['device'] + '/get',
                       json.dumps({profile.get('relay_get_key', 'state'): ''}),
                       qos=1).wait_for_publish(5)
        if not fresh.wait(14):
            raise TimeoutError('Fresh target GET response missing')
        nested = (state['target'] or {}).get('device') or {}
        if nested.get('ieeeAddr') != profile['ieee']:
            raise ValueError('Fresh target GET IEEE mismatch')

        quiet_until = time.monotonic() + observe_seconds
        while time.monotonic() < quiet_until:
            if state['update_seen']:
                raise ValueError('Target still reports an active OTA; reconciliation refused')
            wake.wait(min(0.5, max(0, quiet_until - time.monotonic())))
            wake.clear()

        inventory_match(state['devices'], profile)
        data = {}
        if verify_candidate:
            transaction = 'bseed-reconcile-' + uuid.uuid4().hex
            state['check_token'] = transaction
            payload = {'id': profile['ieee'], 'url': profile['index_url'],
                       'transaction': transaction}
            client.publish(base + '/bridge/request/device/ota_update/check',
                           json.dumps(payload), qos=1).wait_for_publish(5)
            if not checked.wait(max(90, int(profile.get('check_timeout_seconds', 90)))):
                raise TimeoutError('Exact OTA availability check timed out')
            response = state['check'] or {}
            data = response.get('data') or {}
            if response.get('status') != 'ok' or data.get('update_available') is not True:
                raise ValueError('Candidate is not still offered as an update')
            if data.get('source') != profile['url']:
                raise ValueError('OTA check returned a different candidate source')

        inventory_match(state['devices'], profile)
        evidence = {
            'result': 'source_unchanged_reconciled',
            'at': now(),
            'ieee': profile['ieee'],
            'device': profile['device'],
            'source_build': profile['preflash_build'],
            'source_role': profile['preflash_role'],
            'candidate_sha256': profile['sha256'],
            'candidate_check_performed': bool(verify_candidate),
            'candidate_verification_deferred': not verify_candidate,
            'update_available': True if verify_candidate else None,
            'candidate_source': data.get('source') if verify_candidate else None,
            'ota_transport_success': False,
            'hardware_acceptance': False,
            'prior_phase': work_lock.get('phase'),
            'quiet_observe_seconds': observe_seconds,
            'fresh_get_ieee_verified': True,
        }
        evidence_path = work / ('source_unchanged_' + uuid.uuid4().hex + '.json')
        with evidence_path.open('x', encoding='utf8') as handle:
            json.dump(evidence, handle, indent=2)
            handle.write('\n')

        work_lock.update(
            phase='source_unchanged_reconciled',
            reconciled_at=evidence['at'],
            reconciliation_evidence=str(evidence_path),
            ota_transport_success=False,
            hardware_acceptance=False,
        )
        work_lock_path.write_text(json.dumps(work_lock, indent=2) + '\n', encoding='utf8')
        update_network_lock(
            network_path, token, 'source_unchanged_reconciled',
            reconciled_at=evidence['at'],
            reconciliation_evidence=str(evidence_path),
        )

        if profile.get('require_pm') is True:
            from bseed_pm_telemetry_guard import release_source_unchanged as release_pm
            release_pm(profile, evidence_path)

        release_source_unchanged(network_path, token)
        return {'evidence': str(evidence_path), **evidence}
    finally:
        client.loop_stop()
        client.disconnect()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--confirm-ieee', required=True)
    parser.add_argument('--observe-seconds', type=int, default=45)
    parser.add_argument('--defer-candidate-check', action='store_true')
    args = parser.parse_args(argv)
    if not 15 <= args.observe_seconds <= 180:
        parser.error('--observe-seconds must be 15..180')
    print(json.dumps(reconcile(
        args.profile,
        args.confirm_ieee,
        args.observe_seconds,
        verify_candidate=not args.defer_candidate_check,
    ), indent=2))


if __name__ == '__main__':
    main()
