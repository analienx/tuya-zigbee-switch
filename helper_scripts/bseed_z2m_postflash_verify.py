"""Read-only post-OTA evidence collector. Transfer success is never device acceptance.

This tool does not pair, reboot, toggle, interview, or update any Zigbee device.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import threading
import time

import paho.mqtt.client as mqtt
import yaml


def evaluate(inventory, state, expected_ieee, expected_role, expected_build):
    """Require fresh identity/role/build and a post-subscription state observation."""
    found = [d for d in (inventory or []) if d.get('ieee_address') == expected_ieee]
    if len(found) != 1:
        return 'unconfirmed', ['exact IEEE missing or ambiguous'], None
    device = found[0]
    issues = []
    if device.get('interview_completed') is not True: issues.append('interview not completed')
    if device.get('type') != expected_role: issues.append('expected role not verified')
    if device.get('software_build_id') != expected_build: issues.append('expected firmware build not verified')
    if not state: issues.append('no live target state event after monitoring began')
    if state and (state.get('device') or {}).get('ieeeAddr') not in (None, expected_ieee):
        issues.append('state event belongs to another IEEE')
    return ('postflash_candidate' if not issues else 'unconfirmed'), issues, device


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', required=True, help='Zigbee2MQTT friendly name')
    p.add_argument('--ieee', required=True, help='Exact 0x IEEE address')
    p.add_argument('--expect-role', required=True, choices=['Router', 'EndDevice'])
    p.add_argument('--expect-build', required=True, help='Exact custom software_build_id')
    p.add_argument('--mqtt-config', required=True, help='Private Zigbee2MQTT YAML with mqtt credentials')
    p.add_argument('--broker', required=True)
    p.add_argument('--output', required=True, help='Private JSON evidence file outside repository')
    p.add_argument('--observe-seconds', type=int, default=20)
    return p.parse_args()


def main():
    args = arguments()
    assert 4 <= args.observe_seconds <= 180, 'Observation window must be 4..180 seconds'
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    device_topic = base + '/' + args.device
    snapshot = {'inventory': None, 'state': None, 'errors': [], 'bridge': None}
    ready = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))

    def on_connect(c, _userdata, _flags, reason, _properties):
        if reason.is_failure: return
        c.subscribe([(base + '/bridge/devices', 1), (base + '/bridge/state', 1),
                     (base + '/bridge/logging', 0), (device_topic, 1)])
        ready.set()

    def on_message(_c, _userdata, message):
        try: data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError): return
        if message.topic == base + '/bridge/devices' and isinstance(data, list):
            snapshot['inventory'] = data
        elif message.topic == base + '/bridge/state':
            snapshot['bridge'] = data.get('state') if isinstance(data, dict) else data
        elif message.topic == device_topic and not message.retain and isinstance(data, dict):
            snapshot['state'] = data
        elif message.topic == base + '/bridge/logging' and isinstance(data, dict):
            line = str(data.get('message', ''))
            if args.device in line and any(word in line.lower() for word in ('failed', 'error', 'timeout', 'interview')):
                snapshot['errors'].append(line[:500])

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, 1883, 10)
    client.loop_start()
    try:
        assert ready.wait(10), 'MQTT connection/subscription failed'
        time.sleep(args.observe_seconds)
    finally:
        client.loop_stop()
        client.disconnect()
    result, problems, target = evaluate(snapshot['inventory'], snapshot['state'], args.ieee,
                                         args.expect_role, args.expect_build)
    if snapshot['bridge'] != 'online': problems.append('Zigbee2MQTT bridge not verified online')
    if snapshot['errors']: problems.append('target-related Zigbee2MQTT errors observed')
    if problems: result = 'unconfirmed'
    evidence = {'observed_at': dt.datetime.now().astimezone().isoformat(),
                'target_name': args.device, 'target_ieee': args.ieee,
                'expected_role': args.expect_role, 'expected_build': args.expect_build,
                'result': result, 'issues': problems, 'bridge': snapshot['bridge'],
                'inventory': ({k: target.get(k) for k in ('ieee_address', 'friendly_name',
                    'type', 'manufacturer', 'model_id', 'software_build_id', 'interview_completed')}
                    if target else None),
                'state': ({k: snapshot['state'].get(k) for k in ('state', 'state_relay',
                    'power', 'current', 'voltage', 'energy', 'linkquality', 'device')}
                    if snapshot['state'] else None), 'errors': snapshot['errors']}
    outfile = Path(args.output)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    assert not outfile.exists(), 'Refuse to overwrite previous evidence'
    outfile.write_text(json.dumps(evidence, indent=2, default=str), encoding='utf8')
    print(json.dumps(evidence, indent=2, default=str), flush=True)
    print('Physical relay, appliance safety, parent/rejoin, metrology and retained bindings '
          'require separate acceptance; candidate does NOT mean final flash acceptance.', flush=True)
    if result != 'postflash_candidate': raise SystemExit(2)


if __name__ == '__main__':
    main()
