"""Bounded target-only non-PM Client reachability gate. No OTA or relay mutation.

Request-correlated backend reads prove downlink response; MQTT state is only a
consistency check. Neither proves physical load safety. Evidence stays private.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import yaml

SAMPLES = 3
MIN_SPACING_S = 25
MAX_LATENCY_S = 12
MAX_EVIDENCE_AGE_S = 600
PROBE_SCHEMA = 1
DEFAULT_PROBE_ENDPOINT = 2
DEFAULT_PROBE_CLUSTER = 'genOnOff'
DEFAULT_PROBE_ATTRIBUTE = 'onOff'


def _probe_command(profile):
    command = profile.get('link_probe_command')
    if command is None:
        return [sys.executable, str(Path(__file__).with_name('bseed_link_probe.py')),
                '--mqtt-config', profile['mqtt_config'], '--broker', profile['broker']]
    if not (isinstance(command, list) and command and
            all(isinstance(part, str) and part for part in command)):
        raise ValueError(
            'Non-PM link gate requires private link_probe_command list; '
            'MQTT state alone is not downlink proof')
    return list(command)


def _expected_probe_value(profile):
    expected = profile['expect_relay']
    if expected == 'ON':
        return {1, True, 'ON', 'on'}
    if expected == 'OFF':
        return {0, False, 'OFF', 'off'}
    raise ValueError('Unsupported expected relay state')


def verify_probe_evidence(evidence, profile, request_id, gate_requested_at):
    endpoint = int(profile.get('link_probe_endpoint', DEFAULT_PROBE_ENDPOINT))
    cluster = profile.get('link_probe_cluster', DEFAULT_PROBE_CLUSTER)
    attribute = profile.get('link_probe_attribute', DEFAULT_PROBE_ATTRIBUTE)
    if not (isinstance(evidence, dict) and evidence.get('schema') == PROBE_SCHEMA
            and evidence.get('passed') is True):
        raise AssertionError('Backend read probe did not pass')
    expected = {
        'request_id': request_id,
        'device': profile['device'],
        'ieee': profile['ieee'],
        'endpoint': endpoint,
        'cluster': cluster,
        'attribute': attribute,
        'response_type': 'readResponse',
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise AssertionError('Backend read probe mismatch: ' + key)
    transaction = evidence.get('transaction')
    if not (type(transaction) is int and 0 <= transaction <= 255):
        raise AssertionError('Backend read probe missing ZCL transaction sequence')
    requested = evidence.get('requested_at')
    responded = evidence.get('response_at')
    if not (type(requested) in (int, float) and
            type(responded) in (int, float) and
            gate_requested_at <= requested <= responded and
            responded - requested <= MAX_LATENCY_S):
        raise AssertionError('Backend read probe timing is invalid')
    if evidence.get('errors'):
        raise AssertionError('Backend read probe reported an error')
    if evidence.get('value') not in _expected_probe_value(profile):
        raise AssertionError('Backend read probe returned wrong relay value')
    return evidence


def run_probe(profile, work, sequence, gate_requested_at):
    request_id = 'bseed-link-' + uuid.uuid4().hex
    output = work / ('.link_probe_' + request_id + '.json')
    command = _probe_command(profile)
    command.extend([
        '--device', profile['device'],
        '--ieee', profile['ieee'],
        '--endpoint', str(profile.get('link_probe_endpoint', DEFAULT_PROBE_ENDPOINT)),
        '--cluster', str(profile.get('link_probe_cluster', DEFAULT_PROBE_CLUSTER)),
        '--attribute', str(profile.get('link_probe_attribute', DEFAULT_PROBE_ATTRIBUTE)),
        '--request-id', request_id,
        '--timeout-seconds', str(MAX_LATENCY_S),
        '--output', str(output),
    ])
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, timeout=MAX_LATENCY_S + 5)
        if completed.returncode != 0:
            raise AssertionError(
                'Backend read probe failed: ' +
                (completed.stderr.strip() or completed.stdout.strip())[:240])
        if not output.is_file():
            raise AssertionError('Backend read probe produced no evidence file')
        evidence = json.loads(output.read_text(encoding='utf8'))
        return verify_probe_evidence(
            evidence, profile, request_id, gate_requested_at)
    finally:
        output.unlink(missing_ok=True)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def verify_record(record, profile, *, now=None, after=None):
    """Fail closed for a foreign, failed, old or pre-OTA-check sample set."""
    now = time.time() if now is None else now
    if not (record.get('schema') == 1 and record.get('passed') is True):
        raise AssertionError('Link gate not passed')
    for k, expected in (('device', profile['device']), ('ieee', profile['ieee']),
                        ('image_sha256', profile['sha256']), ('build', profile['preflash_build'])):
        if not (record.get(k) == expected):
            raise AssertionError('Link gate identity/image/build mismatch: ' + k)
    if not (len(record.get('samples', [])) == SAMPLES):
        raise AssertionError('Missing link samples')
    times = [s['requested_at'] for s in record['samples']]
    if not (all((
            s['valid'] is True
            and s.get('backend_proven') is True
            and isinstance(s.get('probe_request_id'), str)
            and s.get('probe_request_id')
            and type(s.get('transaction')) is int
            and 0 <= s['transaction'] <= 255
            and s.get('response_type') == 'readResponse'
            and 0 <= s['latency_s'] <= MAX_LATENCY_S
            for s in record['samples']))):
        raise AssertionError('Unresponsive, uncorrelated, or invalid link sample')
    if not (all((b - a >= MIN_SPACING_S for a, b in zip(times, times[1:])))):
        raise AssertionError('Link samples too close')
    if not (len({s.get('probe_request_id') for s in record['samples']}) == SAMPLES):
        raise AssertionError('Link samples reuse probe evidence')
    completed = record['completed_at']
    if not (0 <= completed - times[-1] <= MAX_LATENCY_S + 5):
        raise AssertionError('Last link sample stale at completion')
    if not (0 <= now - completed <= MAX_EVIDENCE_AGE_S):
        raise AssertionError('Link gate expired or future-dated')
    if after is not None:
        if not (times[0] > after):
            raise AssertionError('Flash requires a NEW link gate after successful OTA check')
    return True


def run(profile, *, count=SAMPLES, spacing=30):
    if not (count == SAMPLES and spacing >= MIN_SPACING_S):
        raise ValueError('Qualification requires three separated samples')
    if not (profile.get('non_pm') is True and profile.get('require_pm') is False):
        raise AssertionError()
    allowed_role = profile['preflash_role'] == 'EndDevice' or (
        profile['preflash_role'] == 'Router' and profile.get('postflash_role') == 'EndDevice')
    if not (allowed_role and (profile['model'], profile['manufacturer']) == ('TS011F-BS', 'o1jzcxou')):
        raise AssertionError('Only pinned non-PM Client is supported')
    if profile.get('relay_get_key') != 'state_relay':
        raise ValueError('Link gate requires pinned state_relay read key')
    _probe_command(profile)  # Mandatory independent downlink proof, before any I/O.
    work = Path(profile['workdir'])
    work.mkdir(parents=True, exist_ok=True)
    # Invalidate any previous pass even if this process is interrupted.
    (work / 'LATEST_LINK_GATE.json').write_text(json.dumps({'schema':1, 'passed':False, 'last_error':'link_gate_started_not_completed'}), encoding='utf8')
    config = yaml.safe_load(Path(profile['mqtt_config']).read_text(encoding='utf-8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    result = dict(schema=1, device=profile['device'], ieee=profile['ieee'],
                  image_sha256=profile['sha256'], build=profile['preflash_build'],
                  started_at=time.time(), samples=[], passed=False, last_error=None)
    mutex = threading.Condition()
    state = {'inventory': None, 'bridge': None, 'info': None, 'response': None,
             'seen_at': 0.0, 'availability': None, 'logs': []}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-link-' + uuid.uuid4().hex)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    def on_connect(c, _user, _flags, reason, _properties):
        if reason.is_failure:
            return
        c.subscribe([(base + '/' + name, 1) for name in (
            'bridge/devices', 'bridge/state', 'bridge/info', 'bridge/logging',
            profile['device'], profile['device'] + '/availability')])
    def on_message(_c, _u, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        with mutex:
            tail = message.topic.removeprefix(base + '/')
            if tail == 'bridge/devices': state['inventory'] = data
            elif tail == 'bridge/state': state['bridge'] = data.get('state') if isinstance(data, dict) else data
            elif tail == 'bridge/info': state['info'] = data
            elif tail == profile['device'] + '/availability': state['availability'] = data
            elif tail == profile['device'] and not message.retain and isinstance(data, dict):
                state['response'], state['seen_at'] = data, time.monotonic()
            elif tail == 'bridge/logging' and isinstance(data, dict):
                msg = str(data.get('message', ''))
                if profile['device'] in msg and any(x in msg.lower() for x in ('fail','timeout','error','parent','route')):
                    state['logs'].append(msg[:300]); state['logs'] = state['logs'][-20:]
            mutex.notify_all()
    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect(profile['broker'], 1883, 10)
        client.loop_start()
        with mutex:
            mutex.wait_for(lambda: state['inventory'] is not None and state['bridge'] is not None
                           and state['info'] is not None, timeout=15)
            if not (state['bridge'] == 'online' and state['info']):
                raise AssertionError('Bridge not ready')
            if not (state['info'].get('permit_join') is False):
                raise AssertionError('Permit join open')
            matches = [d for d in (state['inventory'] or [])
                       if d.get('ieee_address') == profile['ieee']]
            if not (len(matches) == 1):
                raise AssertionError('Target IEEE missing or duplicated')
            d = matches[0]
            if not ((d.get('friendly_name'), d.get('manufacturer'), d.get('model_id'), d.get('type'), d.get('software_build_id'), d.get('interview_state')) == (profile['device'], profile['manufacturer'], profile['model'], profile['preflash_role'], profile['preflash_build'], 'SUCCESSFUL')):
                raise AssertionError('Target inventory mismatch')
            result['retained_availability_is_not_proof'] = state['availability']
            result['inventory_snapshot'] = {k:d.get(k) for k in ('network_address','last_seen',
                'linkquality','software_build_id','type','interview_state')}
        for i in range(count):
            if i: time.sleep(spacing)
            with mutex:
                state['response'] = None
                state['seen_at'] = 0
                requested_at, start = time.time(), time.monotonic()
            # Request-correlated downlink: elicit a fresh read response for
            # this sample; unsolicited telemetry alone never suffices.
            client.publish(base + '/' + profile['device'] + '/get',
                           json.dumps({profile['relay_get_key']: ''}),
                           qos=1).wait_for_publish(5)

            probe = run_probe(profile, work, i + 1, requested_at)

            with mutex:
                remaining = max(0.0, MAX_LATENCY_S - (time.monotonic() - start))
                got = (state['seen_at'] >= start or
                       mutex.wait_for(lambda: state['seen_at'] >= start,
                                      timeout=remaining))
                value = state['response'] if got else None

            mqtt_consistent = bool(isinstance(value, dict)
                and (value.get('device') or {}).get('ieeeAddr') == profile['ieee']
                and value.get('state_relay') == profile['expect_relay']
                and value.get('relay_physical_mode') == profile['preflash_relay_physical_mode']
                and value.get('device', {}).get('softwareBuildID') == profile['preflash_build']
                and 'power' not in value)
            latency = probe['response_at'] - probe['requested_at']
            valid = bool(mqtt_consistent)
            result['samples'].append(dict(
                sequence=i + 1,
                requested_at=requested_at,
                valid=valid,
                backend_proven=True,
                probe_request_id=probe['request_id'],
                transaction=probe['transaction'],
                response_type=probe['response_type'],
                probe_requested_at=probe['requested_at'],
                probe_response_at=probe['response_at'],
                latency_s=round(latency, 3),
                relay=value.get('state_relay') if value else None,
                error=None if valid else 'backend_read_proven_but_mqtt_state_inconsistent_or_missing',
            ))
            if not valid:
                result['last_error'] = result['samples'][-1]['error']
                break
        result['completed_at'] = time.time()
        result['passed'] = len(result['samples']) == SAMPLES and all(s['valid'] for s in result['samples'])
        if result['passed']:
            verify_record(result, profile)
    except Exception as exc:
        result['passed'] = False
        result['completed_at'] = time.time()
        result['last_error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['diagnostic_logs'] = list(state['logs'])
        client.loop_stop(); client.disconnect()
        immutable = work / ('link_gate_' + uuid.uuid4().hex + '.json')
        with immutable.open('x', encoding='utf8') as fh:
            json.dump(result, fh, indent=2)
        pointer = work / 'LATEST_LINK_GATE.json'
        pointer.write_text(json.dumps(result, indent=2), encoding='utf8')
        print(json.dumps({'passed':result['passed'], 'evidence':str(immutable),
              'samples':result['samples'], 'reason':result['last_error']}, indent=2), flush=True)
    return result['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='PRIVATE campaign profile outside git')
    parser.add_argument('--spacing-seconds', type=int, default=30)
    args = parser.parse_args()
    from bseed_ota_campaign import load_profile
    profile = load_profile(args.profile)
    raise SystemExit(0 if run(profile, spacing=args.spacing_seconds) else 2)


if __name__ == '__main__':
    main()
