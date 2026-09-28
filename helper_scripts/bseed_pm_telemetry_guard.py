"""Quarantine PM decoding across OTA until fresh, campaign-matched identity evidence.

Called by the canonical campaign only. Failures leave telemetry suppressed;
they never imply a successful update and never permit an automatic reflash.
"""
import datetime as dt
import json
from pathlib import Path

import yaml

from bseed_pm_provision import Bridge, ROOT

OPTION = 'bseed_pm_telemetry_quarantine'


def guard_path(profile):
    path = Path(profile['workdir']).resolve() / 'PM_TELEMETRY_GUARD.json'
    if path.is_relative_to(ROOT):
        raise ValueError('telemetry guard evidence must be private')
    return path


def bridge_for(profile):
    config = yaml.safe_load(Path(profile['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    return Bridge(config, profile['broker'], profile['device'])


def target(bridge, profile):
    matches = [d for d in (bridge.inventory or []) if d.get('ieee_address') == profile['ieee']]
    if bridge.state != 'online' or len(matches) != 1:
        raise ValueError('telemetry guard requires online bridge and one exact target')
    device = matches[0]
    if (device.get('friendly_name'), device.get('manufacturer'), device.get('model_id')) != (
            profile['device'], 'b28wrpvx', 'TS011F-BS-PM'):
        raise ValueError('telemetry guard requires the exact custom PM socket')
    options = (device.get('definition') or {}).get('options') or []
    if not any(o.get('property') == OPTION for o in options):
        raise ValueError('install the candidate converter with telemetry quarantine before OTA')
    return device


def set_option(bridge, profile, enabled):
    response = bridge.request('options', {'id': profile['ieee'], 'options': {OPTION: enabled}},
                              full_response=True)
    data = response.get('data') or {}
    if (data.get('id') != profile['ieee'] or (data.get('to') or {}).get(OPTION) is not enabled
            or data.get('restart_required') is not False):
        raise ValueError('telemetry option change was not acknowledged as effective')
    return data


def begin(profile, campaign_token):
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    if path.exists():
        raise ValueError('existing telemetry guard requires identity recovery before another OTA')
    bridge = bridge_for(profile)
    try:
        bridge.start()
        target(bridge, profile)
        record = dict(ieee=profile['ieee'], device=profile['device'], sha256=profile['sha256'], token=campaign_token,
                      expected_build=profile['postflash_build'], expected_role=profile['postflash_role'],
                      started=dt.datetime.now(dt.timezone.utc).isoformat(), phase='enabling')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf8') as handle:
            json.dump(record, handle, indent=2)
        data = set_option(bridge, profile, True)
        if (data.get('from') or {}).get(OPTION) is True:
            raise ValueError('telemetry was already quarantined; do not take over another guard')
        record['phase'] = 'enabled'
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
    finally:
        bridge.stop()


def validate_release(profile, record, evidence, lock):
    if (record.get('ieee'), record.get('sha256'), record.get('expected_build'),
        record.get('expected_role'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], profile['postflash_build'], profile['postflash_role'], 'enabled'):
        raise ValueError('telemetry guard does not match this enabled campaign')
    if (lock.get('ieee'), lock.get('sha256'), lock.get('token'), lock.get('phase')) != (
            profile['ieee'], profile['sha256'], record.get('token'), 'ota_transfer_ok_postflash_unverified'):
        raise ValueError('telemetry release requires this campaign transfer to have succeeded')
    observed = evidence.get('at') or evidence.get('observed_at')
    if not observed or dt.datetime.fromisoformat(observed) < dt.datetime.fromisoformat(record['started']):
        raise ValueError('identity evidence predates telemetry quarantine')
    if evidence.get('result') == 'build_refreshed_postflash_unverified':
        if evidence.get('target') != profile['ieee'] or evidence.get('fresh_inventory_observed') is not True:
            raise ValueError('fresh target interview evidence missing')
        identity = evidence.get('after') or {}
    elif evidence.get('result') in ('metadata_refreshed', 'metadata_already_correct'):
        if evidence.get('error') is not None:
            raise ValueError('metadata recovery reported an error; PM telemetry stays quarantined')
        if evidence.get('ieee') != profile['ieee']:
            raise ValueError('metadata recovery targeted another device')
        if evidence.get('interview_ok') is not True:
            raise ValueError('metadata recovery lacks a transaction-matched successful interview')
        if evidence.get('fresh_inventory_observed') is not True:
            raise ValueError('metadata recovery lacks fresh nonretained inventory')
        if (evidence.get('live_zdo_after') or {}).get('role') != profile['postflash_role']:
            raise ValueError('final live role evidence missing after metadata recovery')
        identity = evidence.get('after') or {}
    else:
        raise ValueError('identity is still unconfirmed; PM telemetry stays quarantined')
    if (identity.get('ieee_address'), identity.get('software_build_id'), identity.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('post-update identity does not match quarantined campaign')


def validate_timeout_reconcile_release(profile, record, evidence, lock):
    """Allow only fresh installed-identity proof to release PM quarantine after
    an OTA response timeout. This never upgrades the OTA/network lock phase and
    never claims transport success."""
    if (record.get('ieee'), record.get('sha256'), record.get('expected_build'),
        record.get('expected_role'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], profile['postflash_build'], profile['postflash_role'], 'enabled'):
        raise ValueError('telemetry guard does not match this enabled campaign')
    if (lock.get('ieee'), lock.get('sha256'), lock.get('token'), lock.get('phase')) != (
            profile['ieee'], profile['sha256'], record.get('token'), 'update_timeout_or_unconfirmed'):
        raise ValueError('timeout reconciliation requires this exact unresolved campaign')
    observed = evidence.get('at') or evidence.get('observed_at')
    if not observed or dt.datetime.fromisoformat(observed) < dt.datetime.fromisoformat(record['started']):
        raise ValueError('identity evidence predates telemetry quarantine')
    if evidence.get('result') not in ('metadata_refreshed', 'metadata_already_correct'):
        raise ValueError('timeout reconciliation requires fresh metadata evidence')
    if evidence.get('error') is not None or evidence.get('ieee') != profile['ieee']:
        raise ValueError('metadata reconciliation targeted another device or reported an error')
    if evidence.get('interview_ok') is not True or evidence.get('fresh_inventory_observed') is not True:
        raise ValueError('timeout reconciliation requires one successful fresh target interview')
    live = evidence.get('live_zdo_after') or {}
    if live.get('role') != profile['postflash_role']:
        raise ValueError('timeout reconciliation lacks final live role evidence')
    identity = evidence.get('after') or {}
    if (identity.get('ieee_address'), identity.get('software_build_id'), identity.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('installed identity does not match quarantined campaign')
    return True


def release_reconciled(profile, evidence_path):
    """Release only PM telemetry quarantine after timeout identity reconciliation.

    The campaign/network lock deliberately stays unresolved. Hardware acceptance
    and permission for another OTA are separate later decisions.
    """
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    validate_timeout_reconcile_release(profile, record, evidence, lock)
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (
                profile['postflash_build'], profile['postflash_role']):
            raise ValueError('current identity changed since timeout reconciliation evidence')
        set_option(bridge, profile, False)
        record.update(
            phase='released_after_timeout_identity_reconcile',
            evidence=str(evidence_path),
            transport_phase_preserved=lock.get('phase'),
        )
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
        path.rename(path.with_name(
            'PM_TELEMETRY_RELEASED_RECONCILED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


def release(profile, evidence_path):
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    validate_release(profile, record, evidence, lock)
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (profile['postflash_build'], profile['postflash_role']):
            raise ValueError('current identity changed since post-update evidence')
        set_option(bridge, profile, False)
        record.update(phase='released', evidence=str(evidence_path))
        # Preserve the record; never leave a released guard looking active.
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
        path.rename(path.with_name('PM_TELEMETRY_RELEASED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()
