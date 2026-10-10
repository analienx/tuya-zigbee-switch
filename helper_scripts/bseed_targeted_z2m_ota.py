"""Fail-closed one-device Zigbee2MQTT OTA campaign. No images or credentials in git.

First run --mode preflight, then --mode check (with a one-entry index),
then --mode flash. Never run two OTA campaigns at once.
"""
# Live checks use explicit exceptions. Retain the conservative optimized-mode
# refusal as defense in depth before imports, I/O or MQTT.
if not __debug__:
    raise RuntimeError('OTA runner requires Python without -O or PYTHONOPTIMIZE')

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
_LIVE_STATUS_LOCK = threading.Lock()
_TERMINAL_LIVE_PHASES = frozenset((
    'ota_transfer_ok_postflash_unverified', 'update_error',
    'update_timeout_or_unconfirmed', 'preflight_abort',
))


def timestamp():
    return dt.datetime.now().astimezone().isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def matches_response(message, token, device, ieee):
    tx = message.get('transaction')
    target = (message.get('data') or {}).get('id')
    return (tx == token and target in (None, device, ieee)) if tx is not None else target in (device, ieee)


def wait_for_check_result(event, seconds):
    if not (seconds >= 70):
        raise AssertionError('Check monitor must outlast Zigbee2MQTT 60-second response timeout')
    return event.wait(seconds)


def new_campaign_allowed(previous):
    # Legacy update_ok is only a transfer result; it must not permit another flash.
    return not previous or previous.get('phase') in (
        'postflash_accepted', 'installed_image_reconciled',
        'source_unchanged_reconciled', 'preflight_abort')


def archive_prior_check(work):
    prior = work / 'LAST_CHECK.json'
    if not prior.exists(): return None
    archive = work / ('CHECK_ARCHIVE_' + uuid.uuid4().hex + '.json')
    prior.replace(archive)
    return archive


def write_live_status(work, **fields):
    """Atomically persist the latest OTA state for detached/status-only views.

    This file is observational only. ACTIVE_LOCK.json and the shared network
    lock remain the campaign authorities.
    """
    path = Path(work) / 'LIVE_STATUS.json'
    # MQTT callbacks and terminal writes run on separate threads. Serialize the
    # entire read/modify/replace so observations cannot lose a terminal result.
    with _LIVE_STATUS_LOCK:
        current = {}
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding='utf8'))
            except (ValueError, OSError):
                current = {}
        if not isinstance(current, dict):
            current = {}
        if 'token' in fields and fields['token'] != current.get('token'):
            current = {}  # Never attach an earlier transaction's error/result.
        if (current.get('phase') in _TERMINAL_LIVE_PHASES and
                fields.get('phase') in ('ota_running', 'device_state')):
            fields['phase'] = current['phase']
        current.update(fields)
        current['observed_at'] = timestamp()
        # Unique temporary files also avoid collisions with another observer
        # process; the campaign/network locks still determine OTA ownership.
        tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        tmp.write_text(json.dumps(current, indent=2, default=str) + '\n', encoding='utf8')
        tmp.replace(path)
        return current


def ota_transport_phase(response):
    if response.get('status') == 'ok': return 'ota_transfer_ok_postflash_unverified'
    if response.get('status') == 'error': return 'update_error'
    return 'update_timeout_or_unconfirmed'



def validate_nonpm_native_ota_image(image, expected_version):
    """Fail closed on corrupt 512K-layout BSEED non-PM Client images."""
    if not (62 + 32 <= len(image) <= 208 * 1024):
        raise AssertionError('Non-PM OTA image length outside conservative 512K slot limit')
    sub_type, sub_len = struct.unpack_from('<HI', image, 56)
    if not (sub_type == 0 and sub_len == len(image) - 62):
        raise AssertionError('Non-PM OTA sub-element length/type mismatch')
    native = image[62:]
    if not (native[6:8] == b']\x02'):
        raise AssertionError('Non-PM Telink OTA magic missing')
    if not (struct.unpack_from('<I', native, 8)[0] == 1414286923):
        raise AssertionError('Non-PM Telink startup flag missing')
    if not (struct.unpack_from('<I', native, 2)[0] == expected_version):
        raise AssertionError('Non-PM embedded version differs from OTA header')
    if not (struct.unpack_from('<I', native, 24)[0] == len(native)):
        raise AssertionError('Non-PM embedded firmware length mismatch')
    if not (struct.unpack_from('<I', native, len(native) - 4)[0] == binascii.crc32(native[:-4]) ^ 4294967295):
        raise AssertionError('Non-PM embedded CRC mismatch')
    return True


def verify_image(args):
    image = Path(args.image).read_bytes()
    if not (len(image) > 64 and digest(image) == args.sha256):
        raise AssertionError('Image SHA/size mismatch')
    header = struct.unpack_from('<I5HIH32sI', image)
    if not (header[0] == 200208670 and header[2] == 56 and (header[9] == len(image))):
        raise AssertionError('Invalid OTA header')
    if not ((header[4], header[5], header[6]) == (args.manufacturer_code, args.image_type, args.file_version)):
        raise AssertionError('Wrong OTA identity')
    native = image
    native_header = header
    if args.native_image:
        native = Path(args.native_image).read_bytes()
        native_header = struct.unpack_from('<I5HIH32sI', native)
        if not (len(native) > 64 and native_header[0] == 200208670 and native_header[2] == 56 and native_header[9] == len(native)):
            raise AssertionError('Invalid native OTA image')
        if not (image[56:] == native[56:]):
            raise AssertionError('Transport wrapper payload differs from native firmware')
    if getattr(args, 'non_pm', False):
        validate_nonpm_native_ota_image(native, native_header[6])
    with urllib.request.urlopen(args.url, timeout=12) as reply:
        if not (reply.status == 200 and digest(reply.read()) == args.sha256):
            raise AssertionError('HTTP image mismatch')
    return image, header


def update_payload(ieee, url, token, max_block_bytes, response_delay_ms=None, request_timeout_ms=600000):
    if not (10 <= max_block_bytes <= 100):
        raise AssertionError('OTA maximum data size must be 10..100 bytes')
    if not (response_delay_ms is None or 0 <= response_delay_ms <= 10000):
        raise AssertionError('OTA response delay must be 0..10000 ms')
    if not (60000 <= request_timeout_ms <= 3600000):
        raise AssertionError('OTA request timeout must be 60000..3600000 ms')
    payload = {'id': ieee, 'url': url, 'transaction': token, 'image_block_request_timeout': request_timeout_ms, 'default_maximum_data_size': max_block_bytes}
    if response_delay_ms:
        payload['image_block_response_delay'] = response_delay_ms
    return payload


def validate_metering_preflight(relay, *, non_pm, model, manufacturer, role, max_reported_watts,
                               nonpm_router_transition=False, ts0726=False):
    """Explicit non-PM/TS0726 exceptions; PM devices must supply a bounded fresh power reading."""
    if non_pm and ts0726:
        raise AssertionError('Conflicting metering exceptions')
    if ts0726:
        if not ((model, manufacturer, role) in (('TS0726-3-BS', 'iedhxgyi', 'Router'),
                                                ('TS0726', '_TZ3002_iedhxgyi', 'Router'))):
            raise AssertionError('TS0726 exception only for BSEED TS0726-3-BS Router')
        if not ('power' not in relay):
            raise AssertionError('TS0726 preflight unexpectedly exposes PM data; inspect identity')
        return None
    if non_pm:
        if not (model == 'TS011F-BS' and manufacturer == 'o1jzcxou' and
                (role == 'EndDevice' or (role == 'Router' and nonpm_router_transition))):
            raise AssertionError('Non-PM exception only for BSEED TS011F-BS Client')
        if not ('power' not in relay):
            raise AssertionError('Non-PM preflight unexpectedly exposes PM data; inspect identity')
        return None
    power = relay.get('power')
    if not (type(power) in (int, float) and math.isfinite(power) and (0 <= power <= max_reported_watts)):
        raise AssertionError('Power missing or above limit')
    return power


def independently_fresh_pm_sample(data, *, baseline_ms, request_ms, received_ms):
    """A composite MQTT message is not proof of a new ZCL meter report.

    The converter's stamp is emitted only from an actual incoming power report.
    The new stamp must strictly advance beyond the baseline seen before
    requesting the read; bounded wall-clock skew alone never grants freshness.
    """
    stamp = data.get('bseed_pm_sample_time_ms')
    watts = data.get('bseed_pm_sample_power_w')
    if not (type(stamp) in (int, float) and type(watts) in (int, float) and
            math.isfinite(stamp) and math.isfinite(watts) and
            stamp > (baseline_ms or 0) and
            request_ms - 5000 <= stamp <= received_ms + 5000):
        return None
    return {'power': watts, 'sample_time_ms': stamp}


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['preflight', 'check', 'flash'], required=True)
    for key in ('device', 'ieee', 'manufacturer', 'model', 'role', 'image', 'sha256', 'url', 'mqtt-config', 'broker', 'workdir'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--manufacturer-code', type=lambda x: int(x, 0), required=True)
    p.add_argument('--image-type', type=lambda x: int(x, 0), required=True)
    p.add_argument('--file-version', type=lambda x: int(x, 0), required=True)
    p.add_argument('--native-image')
    p.add_argument('--campaign-profile', help='Canonical private profile required for check/flash')
    p.add_argument('--index-url', help='Single-entry OTA JSON index URL; required for read-only --mode check')
    p.add_argument('--expect-relay', choices=['ON', 'OFF'], required=True)
    p.add_argument('--relay-get-key', choices=['state','state_relay'], default='state')
    p.add_argument('--max-reported-watts', type=float, default=1.0)
    p.add_argument('--pm-preflash-physical-unloaded', action='store_true', help='Exact custom PM board only: defer live metering to postflash; require physically unloaded attestation for flash')
    p.add_argument('--non-pm', action='store_true', help='Strict non-PM TS011F-BS Client exception; never use for PM devices')
    p.add_argument('--ts0726', action='store_true', help='Strict TS0726-3-BS Router exception (no metering hardware); never use for PM devices')
    p.add_argument('--hardware-evidence', help='Private exact-board recovery readback attestation, non-PM flash only')
    p.add_argument('--confirm-load-unplugged', action='store_true', help='Operator has just physically verified no appliance attached; required for PM physical-proof flash')
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
    campaign = None
    if args.mode in ('check', 'flash'):
        if not args.campaign_profile:
            raise ValueError('Use the canonical campaign with a private profile for check/flash')
        from bseed_ota_campaign import load_profile
        from bseed_socket_version_policy import require_increasing, number
        campaign = load_profile(args.campaign_profile)
        for key, value in (('device', args.device), ('ieee', args.ieee), ('manufacturer', args.manufacturer),
                           ('model', args.model), ('preflash_role', args.role), ('sha256', args.sha256),
                           ('expect_relay', args.expect_relay), ('url', args.url), ('index_url', args.index_url)):
            if campaign.get(key) != value:
                raise ValueError('Runner differs from canonical profile: ' + key)
        for key, value in (('image', args.image), ('workdir', args.workdir)):
            if Path(campaign[key]).resolve() != Path(value).resolve():
                raise ValueError('Runner path differs from canonical profile: ' + key)
        native_arg = getattr(args, 'native_image', None)
        if campaign.get('native_image'):
            if not native_arg or Path(campaign['native_image']).resolve() != Path(native_arg).resolve():
                raise ValueError('Runner path differs from canonical profile: native_image')
        elif native_arg:
            raise ValueError('Runner supplied native_image absent from canonical profile')
        for key, value in (('manufacturer_code', args.manufacturer_code), ('image_type', args.image_type),
                           ('file_version', args.file_version)):
            if number(campaign[key]) != value:
                raise ValueError('Runner tuple differs from canonical profile: ' + key)
        if args.non_pm != (campaign.get('non_pm') is True):
            raise ValueError('Runner board mode differs from canonical profile')
        if args.pm_preflash_physical_unloaded != (campaign.get('pm_preflash_load_proof') == 'physically_unloaded'):
            raise ValueError('Runner PM load proof differs from canonical profile')
        from bseed_socket_version_policy import split_manufacturer as _split_board
        if getattr(args, 'ts0726', False) != (_split_board(campaign.get('manufacturer', ''))[0] == 'iedhxgyi'):
            raise ValueError('Runner TS0726 mode differs from canonical profile')
        if args.non_pm and getattr(args, 'ts0726', False):
            raise ValueError('Runner metering exceptions are mutually exclusive')
        require_increasing(campaign)
    if args.pm_preflash_physical_unloaded and not (args.manufacturer == 'b28wrpvx' and
            args.model == 'TS011F-BS-PM' and not args.non_pm and not args.ts0726):
        raise ValueError('Physical PM preflight requires exact custom BSEED PM board')
    if args.mode == 'flash' and args.pm_preflash_physical_unloaded and not args.confirm_load_unplugged:
        raise ValueError('PM physical-proof OTA requires explicit load-unplugged confirmation')
    cross_role = campaign is not None and campaign['preflash_role'] != campaign['postflash_role']
    source_profile = campaign
    if not (10 <= args.max_block_bytes <= 100):
        raise AssertionError('OTA maximum data size must be 10..100 bytes')
    response_delay_ms = getattr(args, 'response_delay_ms', None)
    request_timeout_ms = getattr(args, 'request_timeout_ms', 600000)
    if not (response_delay_ms is None or 0 <= response_delay_ms <= 10000):
        raise AssertionError('OTA response delay must be 0..10000 ms')
    if not (60000 <= request_timeout_ms <= 3600000):
        raise AssertionError('OTA request timeout must be 60000..3600000 ms')
    if not (args.check_timeout_seconds >= 70):
        raise AssertionError('OTA check wait must outlast Zigbee2MQTT 60-second device timeout')
    if args.non_pm and args.mode == 'flash':
        from bseed_nonpm_recovery_gate import verify_recovery, verify_transition_recovery
        recovery_gate = verify_transition_recovery if cross_role else verify_recovery
        if (args.max_block_bytes != campaign.get('block_bytes') or
                args.hardware_evidence != str(campaign.get('recovery_evidence', '')) or
                args.preflash_build != campaign.get('preflash_build') or
                args.preflash_relay_physical_mode != campaign.get('preflash_relay_physical_mode')):
            raise ValueError('Runner recovery inputs differ from canonical profile')
        recovery_gate(campaign, confirm_unloaded=args.confirm_load_unplugged)
    elif args.hardware_evidence or (args.confirm_load_unplugged and not (
            args.mode == 'flash' and args.pm_preflash_physical_unloaded)):
        raise ValueError('Hardware recovery evidence is non-PM-only; physical PM confirmation requires pinned flash')
    verify_image(args)
    work = Path(args.workdir); work.mkdir(parents=True, exist_ok=True)
    if args.mode == 'check':
        archive_prior_check(work)
    if args.non_pm and args.mode in ('check', 'flash'):
        from bseed_nonpm_link_gate import verify_record
        if not (args.preflash_build and args.preflash_relay_physical_mode):
            raise AssertionError('Missing pinned Client build/policy')
        evidence = work / 'LATEST_LINK_GATE.json'
        if not (evidence.is_file()):
            raise AssertionError('Missing mandatory non-PM link gate; run campaign --mode link-gate')
        gate_profile = dict(device=args.device, ieee=args.ieee, sha256=args.sha256, preflash_build=args.preflash_build)
        after = None
        if args.mode == 'flash':
            checked = work / 'LAST_CHECK.json'
            if not (checked.is_file()):
                raise AssertionError('OTA availability check missing')
            after = json.loads(checked.read_text(encoding='utf8'))['timestamp']
        verify_record(json.loads(evidence.read_text(encoding='utf8')), gate_profile, after=after)
    lock = work / 'ACTIVE_LOCK.json'
    old = json.loads(lock.read_text()) if lock.exists() else {}
    pending = old.get('phase') == 'source_verified_candidate_pending'
    if pending:
        if args.mode not in ('preflight', 'check') or campaign is None:
            raise AssertionError('Candidate-pending forbids flash; fresh exact check is required')
        from bseed_network_campaign_lock import read_lock as read_network_lock
        from bseed_ota_campaign import network_lock_path as shared_network_lock_path
        owner = read_network_lock(shared_network_lock_path(source_profile, required=True))
        if not owner or (owner.get('token'), owner.get('device'), owner.get('ieee'), owner.get('image_sha256'),
                         owner.get('phase')) != (old.get('token'), args.device, args.ieee, args.sha256,
                                                 'source_verified_candidate_pending'):
            raise AssertionError('Candidate-pending requires original intact network lock')
        if (old.get('device'), old.get('ieee'), old.get('sha256')) != (
                args.device, args.ieee, args.sha256):
            raise AssertionError('Candidate-pending work lock identity mismatch')
    elif not new_campaign_allowed(old):
        raise AssertionError('Previous OTA incomplete, failed, or not postflash-accepted; inspect device and reconcile lock manually before another campaign')
    if args.mode == 'check':
        if not (args.index_url):
            raise AssertionError('check requires one-entry index URL')
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf-8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    token = 'bseed-ota-' + uuid.uuid4().hex
    state = {'inventory': None, 'info': None, 'bridge': None, 'relay': None,
             'check': None, 'result': None, 'sent': False,
             'network_updates': {}, 'pm_baseline_stamp': None, 'pm_fresh': None,
             'pm_request_started_ms': None}
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
        topics = [(base + '/' + suffix, 1) for suffix in (
            'bridge/devices', 'bridge/info', 'bridge/state',
            args.device, 'bridge/logging')]
        # Retained top-level device states are a secondary network-wide OTA
        # observation. The atomic shared network lock remains the authority.
        topics.append((base + '/+', 1))
        topics.append((resp, 1))
        c.subscribe(topics); ready.set()
    def on_message(c, u, message):
        try: data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError): return
        topic = message.topic
        tail = topic.removeprefix(base + '/')
        if isinstance(data, dict) and '/' not in tail:
            update = data.get('update')
            if isinstance(update, dict) and update.get('state') == 'updating':
                state['network_updates'][tail] = True
            else:
                state['network_updates'].pop(tail, None)
            changed.set()
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
                    if state['pm_request_started_ms'] is not None:
                        sample = independently_fresh_pm_sample(
                            data, baseline_ms=state['pm_baseline_stamp'],
                            request_ms=state['pm_request_started_ms'],
                            received_ms=time.time() * 1000)
                        if sample is not None:
                            state['pm_fresh'] = sample
                            changed.set()
                update_state = data.get('update')
                if isinstance(update_state, dict):
                    write_live_status(
                        work, device=args.device, ieee=args.ieee,
                        token=token if state['sent'] else None,
                        phase='ota_running' if update_state.get('state') == 'updating' else 'device_state',
                        update=update_state,
                    )
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
    network_lock_path = None
    network_lock_owned = False
    client.connect(args.broker, 1883, 10); client.loop_start()
    try:
        if not (ready.wait(10)):
            raise AssertionError('MQTT subscription failed')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not all(state[k] is not None for k in ('inventory', 'info', 'bridge')):
            changed.wait(1); changed.clear()
        if not (state['bridge'] == 'online'):
            raise AssertionError('Bridge not online')
        if not (isinstance(state['inventory'], list) and isinstance(state['info'], dict)):
            raise AssertionError('Missing bridge inventory/info')
        matches = [d for d in state['inventory'] if d.get('ieee_address') == args.ieee]
        if not (len(matches) == 1):
            raise AssertionError('IEEE missing or duplicated')
        d = matches[0]
        if not (d.get('friendly_name') == args.device):
            raise AssertionError('Friendly name does not match IEEE')
        if not ((d.get('manufacturer'), d.get('model_id'), d.get('type')) == (args.manufacturer, args.model, args.role)):
            raise AssertionError('Identity/role mismatch')
        if not (d.get('interview_state') == 'SUCCESSFUL'):
            raise AssertionError('Device interview incomplete')
        if campaign and campaign.get('preflash_build') and d.get('software_build_id') != campaign['preflash_build']:
            raise ValueError('Live source build differs from version-policy baseline')
        if not (state['info'].get('permit_join') is False):
            raise AssertionError('Permit join unexpectedly open')
        log('inventory_ok', {k: d.get(k) for k in ('friendly_name', 'ieee_address', 'manufacturer', 'model_id', 'type', 'software_build_id')})
        state['relay'] = None
        client.publish(base + '/' + args.device + '/get', json.dumps({args.relay_get_key: ''}), qos=1).wait_for_publish(5)
        until = time.monotonic() + 14
        while time.monotonic() < until and not fresh_relay.is_set():
            changed.wait(0.5); changed.clear()
        relay = state['relay'] or {}
        if not (relay.get(args.relay_get_key) == args.expect_relay):
            raise AssertionError('Relay state not verified by read-only GET')
        if args.non_pm:
            if not (relay.get('relay_physical_mode') == args.preflash_relay_physical_mode):
                raise AssertionError('Non-PM relay policy changed')
        if not ((relay.get('device') or {}).get('ieeeAddr') == args.ieee):
            raise AssertionError('Fresh MQTT response IEEE mismatch')
        if pending or (old.get('phase') == 'source_unchanged_reconciled' and args.mode == 'flash'):
            fresh_id = relay.get('device') or {}
            if campaign and (fresh_id.get('softwareBuildID'), fresh_id.get('type')) != (
                    campaign['preflash_build'], campaign['preflash_role']):
                raise AssertionError('Fresh target source firmware/role changed since source reconciliation')

        meter_input = relay
        if campaign and campaign.get('require_pm') is True and not args.pm_preflash_physical_unloaded:
            # Force an actual PM ZCL read. A fresh relay GET containing cached
            # "power":0 is NOT fresh load evidence. The converter stamps raw
            # measurement arrivals, rather than unrelated composite updates.
            prior_stamp = relay.get('bseed_pm_sample_time_ms')
            state['pm_baseline_stamp'] = prior_stamp if type(prior_stamp) in (int, float) else 0
            state['pm_request_started_ms'] = time.time() * 1000
            state['pm_fresh'] = None
            client.publish(base + '/' + args.device + '/get',
                           json.dumps({'power': ''}), qos=1).wait_for_publish(5)
            meter_deadline = time.monotonic() + 16
            while time.monotonic() < meter_deadline and state['pm_fresh'] is None:
                changed.wait(min(0.3, max(0, meter_deadline - time.monotonic())))
                changed.clear()
            if state['pm_fresh'] is None:
                raise AssertionError('No newly decoded ZCL activePower sample; cached PM state cannot authorize OTA')
            meter_input = {'power': state['pm_fresh']['power']}
        if args.pm_preflash_physical_unloaded:
            # No guessed zero watts. The flash-only human physical-unloaded
            # attestation replaces live PM input; postflash metering is mandatory.
            power = None
            load_proof = 'physically_unloaded_confirmed' if args.mode == 'flash' else 'requires_physical_confirmation_at_flash'
        else:
            power = validate_metering_preflight(meter_input, non_pm=args.non_pm, model=args.model,
                manufacturer=args.manufacturer, role=args.role, max_reported_watts=args.max_reported_watts,
                nonpm_router_transition=cross_role and args.role == 'Router', ts0726=getattr(args, 'ts0726', False))
            load_proof = 'fresh_pm' if campaign and campaign.get('require_pm') is True else 'board_specific_nonpm'
        if not (not (relay.get('update') or {}).get('state') == 'updating'):
            raise AssertionError('Device OTA already running')
        log('preflight_ok', {'relay': relay.get(args.relay_get_key), 'relay_get_key': args.relay_get_key,
                             'load_proof': load_proof, 'reported_power_w': power, 'fresh_pm_sample': state['pm_fresh'],
                             'voltage_v': relay.get('voltage'), 'image_sha256': args.sha256, 'mode': args.mode})
        if args.mode == 'preflight': return
        if args.mode == 'check':
            payload = {'id': args.ieee, 'url': args.index_url, 'transaction': token}
            state['sent'] = True
            client.publish(req, json.dumps(payload), qos=1).wait_for_publish(5)
            if not (wait_for_check_result(answered, args.check_timeout_seconds)):
                raise AssertionError('Read-only OTA index check timed out before a Zigbee2MQTT result')
            result = state['result'] or {}
            if not (result.get('status') == 'ok' and result.get('data', {}).get('update_available') is True):
                raise AssertionError('OTA check did not offer an update')
            if not (result['data'].get('source') == args.url):
                raise AssertionError('OTA index offered a different image URL')
            if pending and (result.get('transaction') != token or
                            (result.get('data') or {}).get('id') != args.ieee):
                raise AssertionError('Deferred check must return exact transaction and IEEE')
            record = {'device': args.device, 'ieee': args.ieee, 'sha256': args.sha256,
                      'timestamp': time.time(), 'transaction': token, 'response': result}
            (work / 'LAST_CHECK.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
            log('check_passed_no_flash', {'status': result['status'], 'data': result.get('data')})
            return
        record = json.loads((work / 'LAST_CHECK.json').read_text(encoding='utf-8'))
        if not (record['device'] == args.device and record['ieee'] == args.ieee and (record['sha256'] == args.sha256)):
            raise AssertionError('Wrong OTA check record')
        if not (time.time() - record['timestamp'] < 1800):
            raise AssertionError('OTA check is older than 30 minutes')
        if not (record['response'].get('status') == 'ok'):
            raise AssertionError('Previous OTA check did not succeed')
        if old.get('phase') == 'source_unchanged_reconciled':
            reconciled_at = old.get('reconciled_at')
            if (not reconciled_at or record['timestamp'] <=
                    dt.datetime.fromisoformat(reconciled_at).timestamp()):
                raise AssertionError('Flash requires a post-reconciliation OTA check')
        if not (args.mode == 'flash'):
            raise AssertionError()

        # Give retained top-level device states a short bounded window to arrive,
        # then fail closed if any device on the network is already updating.
        changed.wait(1.5); changed.clear()
        inventory_updating = [
            item.get('friendly_name') or item.get('ieee_address')
            for item in state['inventory']
            if isinstance(item, dict)
            and isinstance(item.get('update'), dict)
            and item['update'].get('state') == 'updating'
        ]
        active_updates = sorted(set(
            list(state['network_updates']) + inventory_updating))
        if active_updates:
            raise RuntimeError(
                'Another Zigbee OTA is active on this network: ' +
                ', '.join(active_updates))

        from bseed_network_campaign_lock import (
            acquire as acquire_network_lock,
            update as update_network_lock,
        )
        from bseed_ota_campaign import network_lock_path as shared_network_lock_path
        network_lock_path = shared_network_lock_path(source_profile, required=True)
        coordinator = (state['info'].get('coordinator') or {}).get('ieee_address')
        if not coordinator or source_profile['network_id'] != coordinator:
            raise ValueError('Network lock identity must match the live coordinator IEEE')
        acquire_network_lock(
            network_lock_path,
            network_id=source_profile['network_id'],
            token=token,
            device=args.device,
            ieee=args.ieee,
            image_sha256=args.sha256,
        )
        network_lock_owned = True

        if source_profile.get('require_pm') is True:
            from bseed_pm_telemetry_guard import begin
            begin(source_profile, token)  # Acknowledged before any OTA request; failures leave telemetry suppressed.
        campaign = {'device': args.device, 'ieee': args.ieee, 'sha256': args.sha256, 'phase': 'preflight', 'token': token, 'started': timestamp(),
                    'network_lock': str(network_lock_path),
                    'preflash_state': {k:relay.get(k) for k in ('state','state_relay','energy','relay_physical_mode')},
                    'relay_get_key':args.relay_get_key}
        if old:
            (work / ('LOCK_ARCHIVE_' + token + '.json')).write_text(json.dumps(old, indent=2), encoding='utf-8')
        with lock.open('x' if not lock.exists() else 'w', encoding='utf-8') as handle:
            json.dump(campaign, handle, indent=2)
        campaign['phase'] = 'ota_running'; lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        update_network_lock(network_lock_path, token, 'ota_running')
        payload = update_payload(args.ieee, args.url, token, args.max_block_bytes, response_delay_ms, request_timeout_ms)
        state['sent'] = True
        pub = client.publish(req, json.dumps(payload), qos=1)
        if not (pub.rc == mqtt.MQTT_ERR_SUCCESS):
            raise AssertionError('OTA publish failed')
        pub.wait_for_publish(5)
        if not (pub.is_published()):
            raise AssertionError('OTA publish not confirmed')
        log('ota_request_sent', {'ieee': args.ieee, 'image_sha256': args.sha256, 'transaction': token, 'default_maximum_data_size': args.max_block_bytes, 'image_block_response_delay': response_delay_ms, 'image_block_request_timeout': request_timeout_ms})
        write_live_status(
            work, device=args.device, ieee=args.ieee, token=token,
            phase='ota_running', image_sha256=args.sha256,
            request_timeout_ms=request_timeout_ms,
            block_bytes=args.max_block_bytes,
        )
        deadline = time.monotonic() + args.timeout_seconds
        while not answered.wait(15) and time.monotonic() < deadline:
            log('ota_pending', {'elapsed_seconds': int(args.timeout_seconds - max(0, deadline - time.monotonic()))})
        result = state['result'] or {}
        campaign['phase'] = ota_transport_phase(result)
        campaign['response'] = result; campaign['completed'] = timestamp()
        lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        update_network_lock(
            network_lock_path, token, campaign['phase'],
            completed=campaign['completed'])
        log('ota_final', {'phase': campaign['phase'], 'response': result})
        write_live_status(
            work, device=args.device, ieee=args.ieee, token=token,
            phase=campaign['phase'], response=result,
        )
        if campaign['phase'] != 'ota_transfer_ok_postflash_unverified':
            raise RuntimeError('OTA not confirmed; inspect log/lock before any retry')
        log('postflash_pending', 'OTA service returned OK; verify firmware build, interview, role, relay and meter separately')
    except BaseException as error:
        if args.mode == 'flash' and 'campaign' in locals() and campaign.get('phase') == 'ota_running':
            campaign['phase'] = 'update_timeout_or_unconfirmed'
            campaign['exception'] = repr(error)
            campaign['completed'] = timestamp()
            lock.write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        if network_lock_owned:
            from bseed_network_campaign_lock import (
                release_preflight_abort,
                update as update_network_lock_on_error,
            )
            if not state['sent']:
                update_network_lock_on_error(
                    network_lock_path, token, 'preflight_abort',
                    exception=repr(error))
                release_preflight_abort(network_lock_path, token)
                network_lock_owned = False
            else:
                phase = ('campaign' in locals() and
                         isinstance(campaign, dict) and
                         campaign.get('phase')) or 'update_timeout_or_unconfirmed'
                update_network_lock_on_error(
                    network_lock_path, token, phase,
                    exception=repr(error))
        write_live_status(
            work, device=args.device, ieee=args.ieee, token=token,
            phase=(campaign.get('phase') if isinstance(locals().get('campaign'), dict)
                   else 'preflight_abort'),
            error=repr(error), ota_was_sent=state['sent'],
        )
        log('campaign_error', {'error': repr(error), 'ota_was_sent': state['sent']})
        raise
    finally:
        client.loop_stop(); client.disconnect()


if __name__ == '__main__':
    main()
