"""Post-update state refresh: replace Zigbee2MQTT's cached values with fresh device reads.

Zigbee2MQTT caches every exposed value and republishes it with each message,
so after a wired flash or OTA it can keep showing pre-update relay, power or
energy numbers for hours (an idle socket has no reportable change). This step
runs after every update:

1. checks that the deployed converter recognises the installed build (an
   unknown build falls back to generic scaling and never stamps PM samples);
2. reads the relay and, for PM boards, power/current/voltage/energy directly
   from the device and records cached -> fresh for each field;
3. optionally asks Zigbee2MQTT for an OTA *version check* (never an update) so
   its update panel shows the installed file version instead of the old one.

A field is only reported as fresh when the device answered; anything else is
listed as unconfirmed rather than shown with its stale value. Refreshed values
are not physical acceptance.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

RELAY_KEYS = ('state', 'state_relay')
PM_KEYS = ('power', 'current', 'voltage', 'energy')
PM_STAMP = 'bseed_pm_sample_time_ms'
PM_STAMP_POWER = 'bseed_pm_sample_power_w'


def refresh_keys(relay_key, require_pm):
    if relay_key not in RELAY_KEYS:
        raise ValueError('relay key must be state or state_relay')
    return [relay_key, 'relay_physical_mode'] + (list(PM_KEYS) if require_pm else [])


def converter_known_builds(converter_text):
    """Build IDs listed in the deployed converter's knownBuilds set."""
    match = re.search(r'knownBuilds\s*=\s*new\s+Set\(\[(.*?)\]\)', converter_text or '', re.S)
    return set(re.findall(r'"([^"]+)"', match.group(1))) if match else set()


def fresh_pm_sample(state, *, baseline_ms, request_ms, received_ms):
    """Only a converter stamp newer than the pre-request baseline proves a new ZCL power reading."""
    stamp = (state or {}).get(PM_STAMP)
    watts = (state or {}).get(PM_STAMP_POWER)
    if not (type(stamp) in (int, float) and type(watts) in (int, float) and
            math.isfinite(stamp) and math.isfinite(watts) and stamp > (baseline_ms or 0) and
            request_ms - 5000 <= stamp <= received_ms + 5000):
        return None
    return {'power': watts, 'sample_time_ms': stamp}


def assess_refresh(cached, fresh, *, keys, expected_build, converter_builds=None,
                   baseline_ms=None, request_ms=None, received_ms=None, require_pm=False):
    """Compare the pre-request cached state with fresh post-request device answers."""
    cached = cached if isinstance(cached, dict) else {}
    fresh = fresh if isinstance(fresh, dict) else {}
    issues, fields = [], {}
    for key in keys:
        fields[key] = {'cached': cached.get(key), 'fresh': fresh.get(key),
                       'answered': key in fresh, 'changed': key in fresh and fresh.get(key) != cached.get(key)}
        if key not in fresh:
            issues.append('no fresh value for ' + key)
    pm_sample = None
    if require_pm:
        pm_sample = fresh_pm_sample(fresh, baseline_ms=baseline_ms, request_ms=request_ms or 0,
                                    received_ms=received_ms or 0)
        if pm_sample is None:
            issues.append('no newly stamped ZCL power reading; power/current/voltage/energy may still be cached')
    converter = None
    if converter_builds is not None:
        converter = expected_build in converter_builds
        if not converter:
            issues.append('deployed converter does not list ' + expected_build +
                          '; it falls back to generic scaling and cannot stamp fresh PM readings')
    return {'fields': fields, 'freshPmSample': pm_sample, 'converterKnowsBuild': converter,
            'issues': issues, 'physicalAcceptance': False}


def ota_check_result(response, device):
    """Interpret a transaction-matched ota_update/check response (version check only)."""
    if not isinstance(response, dict) or response.get('status') != 'ok':
        return {'checked': False, 'error': (response or {}).get('error', 'no response')}
    data = response.get('data') or {}
    if data.get('id') not in (None, device):
        return {'checked': False, 'error': 'response is for another device'}
    return {'checked': True, 'updateAvailable': data.get('update_available')}


def run_refresh(client, base, device, *, keys, cached, request_pm, observe_seconds, inbox, changed,
                ota_check=False):
    """Issue reads on an already-subscribed client; inbox holds non-retained device-topic states.

    Returns (fresh_state, request_ms, received_ms, ota_response).
    """
    topic = base + '/' + device
    inbox.clear()
    request_ms = time.time() * 1000
    client.publish(topic + '/get', json.dumps({key: '' for key in keys if key not in PM_KEYS}),
                   qos=1, retain=False).wait_for_publish(5)
    if request_pm:
        # One read per cluster: activePower/current/voltage share haElectricalMeasurement.
        client.publish(topic + '/get', json.dumps({'power': ''}), qos=1, retain=False).wait_for_publish(5)
        client.publish(topic + '/get', json.dumps({'energy': ''}), qos=1, retain=False).wait_for_publish(5)
    deadline = time.monotonic() + observe_seconds
    while time.monotonic() < deadline:
        changed.wait(min(0.5, max(0, deadline - time.monotonic())))
        changed.clear()
    fresh, received_ms = {}, time.time() * 1000
    for state in inbox:
        fresh.update(state)
    ota_response = None
    if ota_check and (cached.get('update') or {}).get('state') != 'updating':
        token = 'bseed-refresh-' + uuid.uuid4().hex
        inbox.ota_token = token
        client.publish(base + '/bridge/request/device/ota_update/check',
                       json.dumps({'id': device, 'transaction': token}), qos=1).wait_for_publish(5)
        ota_deadline = time.monotonic() + 30
        while time.monotonic() < ota_deadline and inbox.ota_response is None:
            changed.wait(0.5)
            changed.clear()
        ota_response = inbox.ota_response
    return fresh, request_ms, received_ms, ota_response


class Inbox(list):
    """Non-retained device states received after the read requests."""
    ota_token = None
    ota_response = None


def add_refresh_arguments(p, standalone=True):
    if standalone:
        p.add_argument('--relay-get-key', choices=RELAY_KEYS, default='state')
        p.add_argument('--require-pm', action='store_true', help='also refresh power/current/voltage/energy')
    p.add_argument('--converter', help='copy of the DEPLOYED switch_custom.js to check for the build')
    p.add_argument('--ota-version-check', action='store_true',
                   help='ask Zigbee2MQTT to re-read the installed OTA version (never starts an update)')
    p.add_argument('--refresh-seconds', type=int, default=20, help='how long to collect fresh answers')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--device', required=True, help='Zigbee2MQTT friendly name')
    p.add_argument('--ieee', required=True, help='exact 0x IEEE address')
    p.add_argument('--expect-build', required=True, help='software_build_id that was just installed')
    p.add_argument('--mqtt-config', required=True, help='private Zigbee2MQTT YAML with mqtt credentials')
    p.add_argument('--broker', required=True)
    p.add_argument('--output', required=True, help='new private JSON evidence file outside the repository')
    add_refresh_arguments(p)
    args = p.parse_args()
    output = Path(args.output).expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]) or output.exists():
        raise SystemExit('use a new private evidence file outside the repository')
    evidence = collect(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, default=str), encoding='utf8')
    print(json.dumps(evidence, indent=2, default=str))
    raise SystemExit(0 if not evidence['issues'] else 2)


def collect(args):
    """Run the refresh for parsed arguments; returns evidence with an issues list."""
    import paho.mqtt.client as mqtt
    import yaml
    if not 5 <= args.refresh_seconds <= 120:
        raise ValueError('refresh-seconds must be 5..120')
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    keys = refresh_keys(args.relay_get_key, args.require_pm)
    converter_builds = (converter_known_builds(Path(args.converter).read_text(encoding='utf8'))
                        if args.converter else None)
    snapshot = {'cached': None, 'inventory': None}
    inbox, changed, ready = Inbox(), threading.Event(), threading.Event()
    collecting = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-refresh-' + uuid.uuid4().hex[:8])
    client.username_pw_set(config.get('user', ''), config.get('password', ''))

    def on_connect(c, _u, _f, reason, _p):
        if reason.is_failure:
            return
        c.subscribe([(base + '/' + args.device, 1), (base + '/bridge/devices', 1),
                     (base + '/bridge/response/device/ota_update/check', 1)])
        ready.set()

    def on_message(_c, _u, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if message.topic == base + '/bridge/devices' and isinstance(data, list):
            snapshot['inventory'] = data
        elif message.topic.endswith('/ota_update/check') and isinstance(data, dict):
            if inbox.ota_token and data.get('transaction') == inbox.ota_token:
                inbox.ota_response = data
        elif message.topic == base + '/' + args.device and isinstance(data, dict):
            if collecting.is_set() and not message.retain:
                inbox.append(data)
            elif snapshot['cached'] is None:
                snapshot['cached'] = data
        changed.set()

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.broker, 1883, 10)
    client.loop_start()
    try:
        if not ready.wait(10):
            raise TimeoutError('MQTT connection/subscription failed')
        time.sleep(2)  # retained device state and inventory, if any
        target = [d for d in snapshot['inventory'] or [] if d.get('ieee_address') == args.ieee]
        if len(target) != 1 or target[0].get('friendly_name') != args.device:
            raise ValueError('exact IEEE/friendly name not found in the live inventory')
        cached = snapshot['cached'] or {}
        collecting.set()
        fresh, request_ms, received_ms, ota_response = run_refresh(
            client, base, args.device, keys=keys, cached=cached, request_pm=args.require_pm,
            observe_seconds=args.refresh_seconds, inbox=inbox, changed=changed,
            ota_check=args.ota_version_check)
    finally:
        client.loop_stop()
        client.disconnect()
    result = assess_refresh(cached, fresh, keys=keys, expected_build=args.expect_build,
                            converter_builds=converter_builds,
                            baseline_ms=cached.get(PM_STAMP), request_ms=request_ms,
                            received_ms=received_ms, require_pm=args.require_pm)
    if args.ota_version_check:
        result['otaVersionCheck'] = ota_check_result(ota_response, args.device)
        if not result['otaVersionCheck']['checked']:
            result['issues'].append('OTA version check failed: ' + str(result['otaVersionCheck'].get('error')))
    if target[0].get('software_build_id') != args.expect_build:
        result['issues'].append('inventory build is %s, expected %s' % (target[0].get('software_build_id'),
                                                                        args.expect_build))
    return {'observedAt': dt.datetime.now().astimezone().isoformat(), 'device': args.device,
            'ieee': args.ieee, 'expectedBuild': args.expect_build,
            'result': 'refreshed' if not result['issues'] else 'incomplete', **result}


if __name__ == '__main__':
    main()
