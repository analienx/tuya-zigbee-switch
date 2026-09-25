"""Fail-closed one-device Zigbee2MQTT OTA campaign. No images or credentials in git.

First run --mode preflight, then --mode check (with a one-entry index),
then --mode flash. Never run two OTA campaigns at once.
"""
import argparse
import binascii
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import struct
import threading
import time
import urllib.request
import uuid

import paho.mqtt.client as mqtt
import yaml

DEFAULT_CHECK_TIMEOUT_SECONDS = 90  # Z2M may take 60 seconds to report a device OTA-query failure.


def timestamp():
    return dt.datetime.now().astimezone().isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def matches_response(message, token, device, ieee):
    tx = message.get('transaction')
    target = (message.get('data') or {}).get('id')
    return (tx == token and target in (None, device, ieee)) if tx is not None else target in (device, ieee)


def wait_for_check_result(event, seconds):
    assert seconds >= 70, 'Check monitor must outlast Zigbee2MQTT 60-second response timeout'
    return event.wait(seconds)


def new_campaign_allowed(previous):
    # Legacy update_ok is only a transfer result; it must not permit another flash.
    return not previous or previous.get('phase') in ('postflash_accepted', 'preflight_abort')


def archive_prior_check(work):
    prior = work / 'LAST_CHECK.json'
    if not prior.exists(): return None
    archive = work / ('CHECK_ARCHIVE_' + uuid.uuid4().hex + '.json')
    prior.replace(archive)
    return archive


def ota_transport_phase(response):
    if response.get('status') == 'ok': return 'ota_transfer_ok_postflash_unverified'
    if response.get('status') == 'error': return 'update_error'
    return 'update_timeout_or_unconfirmed'



def validate_nonpm_native_ota_image(image, expected_version):
    """Fail closed on corrupt 512K-layout BSEED non-PM Client images."""
    assert 62 + 32 <= len(image) <= 208 * 1024, 'Non-PM OTA image length outside conservative 512K slot limit'
    sub_type, sub_len = struct.unpack_from('<HI', image, 56)
    assert sub_type == 0 and sub_len == len(image) - 62, 'Non-PM OTA sub-element length/type mismatch'
    native = image[62:]
    assert native[6:8] == b'\x5d\x02', 'Non-PM Telink OTA magic missing'
    assert struct.unpack_from('<I', native, 8)[0] == 0x544c4e4b, 'Non-PM Telink startup flag missing'
    assert struct.unpack_from('<I', native, 2)[0] == expected_version, 'Non-PM embedded version differs from OTA header'
    assert struct.unpack_from('<I', native, 0x18)[0] == len(native), 'Non-PM embedded firmware length mismatch'
    assert struct.unpack_from('<I', native, len(native)-4)[0] == (binascii.crc32(native[:-4]) ^ 0xffffffff), 'Non-PM embedded CRC mismatch'
    return True


def verify_image(args):
    image = Path(args.image).read_bytes()
    assert len(image) > 64 and digest(image) == args.sha256, 'Image SHA/size mismatch'
    header = struct.unpack_from('<I5HIH32sI', image)
    assert header[0] == 0x0BEEF11E and header[2] == 56 and header[9] == len(image), 'Invalid OTA header'
    assert (header[4], header[5], header[6]) == (args.manufacturer_code, args.image_type, args.file_version), 'Wrong OTA identity'
    if getattr(args, 'non_pm', False): validate_nonpm_native_ota_image(image, args.file_version)
    if args.native_image:
        native = Path(args.native_image).read_bytes()
        assert image[56:] == native[56:], 'Stock wrapper payload differs from native firmware'
    with urllib.request.urlopen(args.url, timeout=12) as reply:
        assert reply.status == 200 and digest(reply.read()) == args.sha256, 'HTTP image mismatch'
    return image, header


def update_payload(ieee, url, token, max_block_bytes, response_delay_ms=None, request_timeout_ms=600000):
    assert 10 <= max_block_bytes <= 100, 'OTA maximum data size must be 10..100 bytes'
    assert response_delay_ms is None or 0 <= response_delay_ms <= 10000, 'OTA response delay must be 0..10000 ms'
    assert 60000 <= request_timeout_ms <= 3600000, 'OTA request timeout must be 60000..3600000 ms'
    payload = {'id': ieee, 'url': url, 'transaction': token, 'image_block_request_timeout': request_timeout_ms, 'default_maximum_data_size': max_block_bytes}
    if response_delay_ms:
        payload['image_block_response_delay'] = response_delay_ms
    return payload


def validate_metering_preflight(relay, *, non_pm, model, manufacturer, role, max_reported_watts):
    """Explicit non-PM exception; PM devices must supply a bounded fresh power reading."""
    if non_pm:
        assert (model, manufacturer, role) == ('TS011F-BS', 'o1jzcxou', 'EndDevice'), 'Non-PM exception only for BSEED TS011F-BS Client'
        assert 'power' not in relay, 'Non-PM preflight unexpectedly exposes PM data; inspect identity'
        return None
    power = relay.get('power')
    assert type(power) in (int, float) and math.isfinite(power) and 0 <= power <= max_reported_watts, 'Power missing or above limit'
    return power


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['preflight', 'check', 'flash'], required=True)
    for key in ('device', 'ieee', 'manufacturer', 'model', 'role', 'image', 'sha256', 'url', 'mqtt-config', 'broker', 'workdir'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--manufacturer-code', type=lambda x: int(x, 0), required=True)
    p.add_argument('--image-type', type=lambda x: int(x, 0), required=True)
    p.add_argument('--file-version', type=lambda x: int(x, 0), required=True)
    p.add_argument('--native-image')
    p.add_argument('--index-url', help='Single-entry OTA JSON index URL; required for read-only --mode check')
    p.add_argument('--expect-relay', choices=['ON', 'OFF'], required=True)
    p.add_argument('--relay-get-key', choices=['state','state_relay'], default='state')
    p.add_argument('--max-reported-watts', type=float, default=1.0)
    p.add_argument('--non-pm', action='store_true', help='Strict non-PM TS011F-BS Client exception; never use for PM devices')
    p.add_argument('--hardware-evidence', help='Private exact-board recovery readback attestation, non-PM flash only')
    p.add_argument('--accept-nonrecoverable-ota-risk', action='store_true', help='One exact-canary non-PM OTA; failure may require replacement')
    p.add_argument('--confirm-load-unplugged', action='store_true', help='Non-PM flash only; operator has just verified no appliance attached')
    p.add_argument('--preflash-build')
    p.add_argument('--preflash-relay-physical-mode')
    p.add_argument('--timeout-seconds', type=int, default=2400)
    p.add_argument('--check-timeout-seconds', type=int, default=DEFAULT_CHECK_TIMEOUT_SECONDS)
    p.add_argument('--max-block-bytes', type=int, default=50, help='OTA per-request maximum; conservative 50-byte default for fragile meshes')
    p.add_argument('--response-delay-ms', type=int, default=None, help='Paced OTA profile for sleepy EndDevice clients: minimum ms between server block responses (Z2M image_block_response_delay); omit for server default')
    p.add_argument('--request-timeout-ms', type=int, default=600000, help='Per-block server wait for the next client request (Z2M image_block_request_timeout); raise for paced transfers so retry storms are not funeraled early')
    return p.parse_args()


def main():
    args = arguments()
    assert 10 <= args.max_block_bytes <= 100, 'OTA maximum data size must be 10..100 bytes'
    response_delay_ms = getattr(args, 'response_delay_ms', None)
    request_timeout_ms = getattr(args, 'request_timeout_ms', 600000)
    assert response_delay_ms is None or 0 <= response_delay_ms <= 10000, 'OTA response delay must be 0..10000 ms'
    assert 60000 <= request_timeout_ms <= 3600000, 'OTA request timeout must be 60000..3600000 ms'
    assert args.check_timeout_seconds >= 70, 'OTA check wait must outlast Zigbee2MQTT 60-second device timeout'
    if args.non_pm and args.mode == 'flash':
        from bseed_nonpm_recovery_gate import verify_recovery
        verify_recovery(dict(non_pm=True, manufacturer=args.manufacturer, model=args.model,
            preflash_role=args.role, postflash_role=args.role, ieee=args.ieee,
            sha256=args.sha256, block_bytes=args.max_block_bytes,
            preflash_build=args.preflash_build,
            recovery_evidence=args.hardware_evidence, device=args.device,
            postflash_build='1.1.2-bseedcli5-rc2', require_pm=False,
            relay_get_key=args.relay_get_key, expect_relay=args.expect_relay,
            preflash_relay_physical_mode=args.preflash_relay_physical_mode),
            confirm_unloaded=args.confirm_load_unplugged,
            accept_nonrecoverable_ota=args.accept_nonrecoverable_ota_risk)
    elif args.hardware_evidence or args.confirm_load_unplugged:
        raise ValueError('Non-PM hardware recovery flags are valid only for non-PM flash')
    verify_image(args)
    work = Path(args.workdir); work.mkdir(parents=True, exist_ok=True)
    if args.mode == 'check':
        archive_prior_check(work)
    if args.non_pm and args.mode in ('check', 'flash'):
        from bseed_nonpm_link_gate import verify_record
        assert args.preflash_build and args.preflash_relay_physical_mode, 'Missing pinned Client build/policy'
        evidence = work / 'LATEST_LINK_GATE.json'
        assert evidence.is_file(), 'Missing mandatory non-PM link gate; run campaign --mode link-gate'
        gate_profile = dict(device=args.device, ieee=args.ieee, sha256=args.sha256, preflash_build=args.preflash_build)
        after = None
        if args.mode == 'flash':
            checked = work / 'LAST_CHECK.json'
            assert checked.is_file(), 'OTA availability check missing'
            after = json.loads(checked.read_text(encoding='utf8'))['timestamp']
        verify_record(json.loads(evidence.read_text(encoding='utf8')), gate_profile, after=after)
    lock = work / 'ACTIVE_LOCK.json'
    old = json.loads(lock.read_text()) if lock.exists() else {}
    assert new_campaign_allowed(old), 'Previous OTA incomplete, failed, or not postflash-accepted; inspect device and reconcile lock manually before another campaign'
    if args.mode == 'check':
        assert args.index_url, 'check requires one-entry index URL'
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf-8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    token = 'bseed-ota-' + uuid.uuid4().hex
    state = {'inventory': None, 'info': None, 'bridge': None, 'relay': None, 'check': None, 'result': None, 'sent': False}
    ready = threading.Event(); changed = threading.Event(); answered = threading.Event(); fresh_relay = threading.Event()
    log_path = work / ('ota_' + token + '.jsonl')
    def log(event, value):
        item = {'when': timestamp(), 'event': event, 'value': value}
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(item, default=str) + '\n')
        print(event, json.dumps(value, default=str)[:1100], flush=True)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=token)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    req = base + '/bridge/request/device/ota_update/' + ('check' if args.mode == 'check' else 'update')
    resp = base + '/bridge/response/device/ota_update/' + ('check' if args.mode == 'check' else 'update')
    def on_connect(c, u, flags, reason, props):
        if reason.is_failure: return
        topics = [(base + '/' + suffix, 1) for suffix in ('bridge/devices', 'bridge/info', 'bridge/state', args.device, 'bridge/logging')]
        topics.append((resp, 1))
        c.subscribe(topics); ready.set()
    def on_message(c, u, message):
        try: data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError): return
        topic = message.topic
        if topic == base + '/bridge/devices':
            state['inventory'] = data; changed.set()
        elif topic == base + '/bridge/info':
            state['info'] = data; changed.set()
        elif topic == base + '/bridge/state':
            state['bridge'] = data.get('state') if isinstance(data, dict) else data; changed.set()
        elif topic == base + '/' + args.device:
            if isinstance(data, dict):
                if not message.retain:
                    state['relay'] = data; fresh_relay.set()
                if state['sent'] and not message.retain:
                    log('device_state', {k: data.get(k) for k in ('state', 'state_relay', 'power', 'current', 'voltage', 'update')})
                changed.set()
        elif topic == resp and state['sent']:
            if not matches_response(data, token, args.device, args.ieee):
                log('ignored_foreign_response', {'target': (data.get('data') or {}).get('id'), 'transaction': data.get('transaction')}); return
            state['result'] = data; log('target_response', data); answered.set()
        elif topic == base + '/bridge/logging' and state['sent']:
            text = str(data.get('message', ''))
            if 'MQTT publish:' not in text and (args.device in text or 'ota' in text.lower()):
                log('z2m_log', {'level': data.get('level'), 'message': text[:400]})
    client.on_connect = on_connect; client.on_message = on_message
    client.connect(args.broker, 1883, 10); client.loop_start()
    try:
        assert ready.wait(10), 'MQTT subscription failed'
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not all(state[k] is not None for k in ('inventory', 'info', 'bridge')):
            changed.wait(1); changed.clear()
        assert state['bridge'] == 'online', 'Bridge not online'
        assert isinstance(state['inventory'], list) and isinstance(state['info'], dict), 'Missing bridge inventory/info'
        matches = [d for d in state['inventory'] if d.get('ieee_address') == args.ieee]
        assert len(matches) == 1, 'IEEE missing or duplicated'
        d = matches[0]
        assert d.get('friendly_name') == args.device, 'Friendly name does not match IEEE'
        assert (d.get('manufacturer'), d.get('model_id'), d.get('type')) == (args.manufacturer, args.model, args.role), 'Identity/role mismatch'
        assert d.get('interview_state') == 'SUCCESSFUL', 'Device interview incomplete'
        assert state['info'].get('permit_join') is False, 'Permit join unexpectedly open'
        log('inventory_ok', {k: d.get(k) for k in ('friendly_name', 'ieee_address', 'manufacturer', 'model_id', 'type', 'software_build_id')})
        state['relay'] = None
        client.publish(base + '/' + args.device + '/get', json.dumps({args.relay_get_key: ''}), qos=1).wait_for_publish(5)
        until = time.monotonic() + 14
        while time.monotonic() < until and not fresh_relay.is_set():
            changed.wait(0.5); changed.clear()
        relay = state['relay'] or {}
        assert relay.get(args.relay_get_key) == args.expect_relay, 'Relay state not verified by read-only GET'
        if args.non_pm:
            assert relay.get('relay_physical_mode') == args.preflash_relay_physical_mode, 'Non-PM relay policy changed'
        assert (relay.get('device') or {}).get('ieeeAddr') == args.ieee, 'Fresh MQTT response IEEE mismatch'
        power = validate_metering_preflight(relay, non_pm=args.non_pm, model=args.model,
            manufacturer=args.manufacturer, role=args.role, max_reported_watts=args.max_reported_watts)
        assert not (relay.get('update') or {}).get('state') == 'updating', 'Device OTA already running'
        log('preflight_ok', {'relay': relay.get(args.relay_get_key), 'relay_get_key': args.relay_get_key, 'reported_power_w': power, 'voltage_v': relay.get('voltage'), 'image_sha256': args.sha256, 'mode': args.mode})
        if args.mode == 'preflight': return
        if args.mode == 'check':
            payload = {'id': args.ieee, 'url': args.index_url, 'transaction': token}
            state['sent'] = True
            client.publish(req, json.dumps(payload), qos=1).wait_for_publish(5)
            assert wait_for_check_result(answered, args.check_timeout_seconds), 'Read-only OTA index check timed out before a Zigbee2MQTT result'
            result = state['result'] or {}
            assert result.get('status') == 'ok' and result.get('data', {}).get('update_available') is True, 'OTA check did not offer an update'
            assert result['data'].get('source') == args.url, 'OTA index offered a different image URL'
            record = {'device': args.device, 'ieee': args.ieee, 'sha256': args.sha256, 'timestamp': time.time(), 'response': result}
            (work / 'LAST_CHECK.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
            log('check_passed_no_flash', {'status': result['status'], 'data': result.get('data')})
            return
        record = json.loads((work / 'LAST_CHECK.json').read_text(encoding='utf-8'))
        assert record['device'] == args.device and record['ieee'] == args.ieee and record['sha256'] == args.sha256, 'Wrong OTA check record'
        assert time.time() - record['timestamp'] < 1800, 'OTA check is older than 30 minutes'
        assert record['response'].get('status') == 'ok', 'Previous OTA check did not succeed'
        assert args.mode == 'flash'
        campaign = {'device': args.device, 'ieee': args.ieee, 'sha256': args.sha256, 'phase': 'preflight', 'token': token, 'started': timestamp(),
                    'preflash_state': {k:relay.get(k) for k in ('state','state_relay','energy','relay_physical_mode')},
                    'relay_get_key':args.relay_get_key}
        if old:
            (work / ('LOCK_ARCHIVE_' + token + '.json')).write_text(json.dumps(old, indent=2), encoding='utf-8')
        with lock.open('x' if not lock.exists() else 'w', encoding='utf-8') as handle:
            json.dump(campaign, handle, indent=2)
        campaign['phase'] = 'ota_running'; lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        payload = update_payload(args.ieee, args.url, token, args.max_block_bytes, response_delay_ms, request_timeout_ms)
        state['sent'] = True
        pub = client.publish(req, json.dumps(payload), qos=1)
        assert pub.rc == mqtt.MQTT_ERR_SUCCESS, 'OTA publish failed'
        pub.wait_for_publish(5)
        assert pub.is_published(), 'OTA publish not confirmed'
        log('ota_request_sent', {'ieee': args.ieee, 'image_sha256': args.sha256, 'transaction': token, 'default_maximum_data_size': args.max_block_bytes, 'image_block_response_delay': response_delay_ms, 'image_block_request_timeout': request_timeout_ms})
        deadline = time.monotonic() + args.timeout_seconds
        while not answered.wait(15) and time.monotonic() < deadline:
            log('ota_pending', {'elapsed_seconds': int(args.timeout_seconds - max(0, deadline - time.monotonic()))})
        result = state['result'] or {}
        campaign['phase'] = ota_transport_phase(result)
        campaign['response'] = result; campaign['completed'] = timestamp()
        lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        log('ota_final', {'phase': campaign['phase'], 'response': result})
        if campaign['phase'] != 'ota_transfer_ok_postflash_unverified':
            raise RuntimeError('OTA not confirmed; inspect log/lock before any retry')
        log('postflash_pending', 'OTA service returned OK; verify firmware build, interview, role, relay and meter separately')
    except BaseException as error:
        if args.mode == 'flash' and 'campaign' in locals() and campaign.get('phase') == 'ota_running':
            campaign['phase'] = 'update_timeout_or_unconfirmed'
            campaign['exception'] = repr(error)
            campaign['completed'] = timestamp()
            lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        log('campaign_error', {'error': repr(error), 'ota_was_sent': state['sent']})
        raise
    finally:
        client.loop_stop(); client.disconnect()


if __name__ == '__main__':
    main()
