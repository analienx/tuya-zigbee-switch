"""Bounded target-only non-PM Client reachability gate. No OTA or relay mutation.

Fresh non-retained MQTT is a communication proxy, NOT proof of ZCL origin or
physical load safety. Evidence stays private in the campaign workdir.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import yaml

SAMPLES = 3
MIN_SPACING_S = 25
MAX_LATENCY_S = 12
MAX_EVIDENCE_AGE_S = 600


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def verify_record(record, profile, *, now=None, after=None):
    """Fail closed for a foreign, failed, old or pre-OTA-check sample set."""
    now = time.time() if now is None else now
    assert record.get('schema') == 1 and record.get('passed') is True, 'Link gate not passed'
    for k, expected in (('device', profile['device']), ('ieee', profile['ieee']),
                        ('image_sha256', profile['sha256']), ('build', profile['preflash_build'])):
        assert record.get(k) == expected, 'Link gate identity/image/build mismatch: ' + k
    assert len(record.get('samples', [])) == SAMPLES, 'Missing link samples'
    times = [s['requested_at'] for s in record['samples']]
    assert all(s['valid'] is True and 0 <= s['latency_s'] <= MAX_LATENCY_S
               for s in record['samples']), 'Unresponsive or invalid link sample'
    assert all(b - a >= MIN_SPACING_S for a, b in zip(times, times[1:])), 'Link samples too close'
    completed = record['completed_at']
    assert 0 <= completed - times[-1] <= MAX_LATENCY_S + 5, 'Last link sample stale at completion'
    assert 0 <= now - completed <= MAX_EVIDENCE_AGE_S, 'Link gate expired or future-dated'
    if after is not None:
        assert times[0] > after, 'Flash requires a NEW link gate after successful OTA check'
    return True


def run(profile, *, count=SAMPLES, spacing=30):
    if not (count == SAMPLES and spacing >= MIN_SPACING_S):
        raise ValueError('Qualification requires three separated samples')
    assert profile.get('non_pm') is True and profile.get('require_pm') is False
    assert (profile['model'], profile['manufacturer'], profile['preflash_role']) == (
        'TS011F-BS', 'o1jzcxou', 'EndDevice'), 'Only pinned non-PM Client is supported'
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
            assert state['bridge'] == 'online' and state['info'], 'Bridge not ready'
            assert state['info'].get('permit_join') is False, 'Permit join open'
            matches = [d for d in (state['inventory'] or [])
                       if d.get('ieee_address') == profile['ieee']]
            assert len(matches) == 1, 'Target IEEE missing or duplicated'
            d = matches[0]
            assert (d.get('friendly_name'), d.get('manufacturer'), d.get('model_id'),
                    d.get('type'), d.get('software_build_id'), d.get('interview_state')) == (
                    profile['device'], profile['manufacturer'], profile['model'],
                    profile['preflash_role'], profile['preflash_build'], 'SUCCESSFUL'), 'Target inventory mismatch'
            result['retained_availability_is_not_proof'] = state['availability']
            result['inventory_snapshot'] = {k:d.get(k) for k in ('network_address','last_seen',
                'linkquality','software_build_id','type','interview_state')}
        for i in range(count):
            if i: time.sleep(spacing)
            with mutex:
                state['response'] = None
                state['seen_at'] = 0
                requested_at, start = time.time(), time.monotonic()
            publish = client.publish(base + '/' + profile['device'] + '/get',
                        json.dumps({'state_relay': ''}), qos=1)
            publish.wait_for_publish(5)
            assert publish.is_published(), 'MQTT publish not confirmed'
            with mutex:
                got = mutex.wait_for(lambda: state['seen_at'] >= start, timeout=MAX_LATENCY_S)
                value = state['response'] if got else None
                latency = state['seen_at'] - start if got else None
            valid = bool(isinstance(value, dict)
                and (value.get('device') or {}).get('ieeeAddr') == profile['ieee']
                and value.get('state_relay') == profile['expect_relay']
                and value.get('relay_physical_mode') == profile['preflash_relay_physical_mode']
                and value.get('device', {}).get('softwareBuildID') == profile['preflash_build']
                and 'power' not in value)
            result['samples'].append(dict(sequence=i + 1, requested_at=requested_at,
                valid=valid, latency_s=round(latency, 3) if got else None,
                relay=value.get('state_relay') if value else None,
                error=None if valid else ('invalid_identity_settings_or_build' if got else 'no_fresh_response')))
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
