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
from bseed_zdo_live import read_node_descriptor
from bseed_z2m_postota_reinterview import validate_campaign


def evaluate(inventory, state, expected_ieee, expected_role, expected_build, live_node=None):
    """Require fresh identity/role/build and a post-subscription state observation."""
    found = [d for d in (inventory or []) if d.get('ieee_address') == expected_ieee]
    if len(found) != 1:
        return 'unconfirmed', ['exact IEEE missing or ambiguous'], None
    device = found[0]
    issues = []
    if device.get('interview_completed') is not True: issues.append('interview not completed')
    if not live_node or live_node.get('role') != expected_role: issues.append('live ZDO role not verified')
    if device.get('software_build_id') != expected_build: issues.append('expected firmware build not verified')
    # A successful live ZDO read proves network responsiveness even if passive MQTT telemetry is quiet.
    if state and (state.get('device') or {}).get('ieeeAddr') not in (None, expected_ieee):
        issues.append('state event belongs to another IEEE')
    return ('postflash_candidate' if not issues else 'unconfirmed'), issues, device


def pm_readiness(device, fresh_state):
    """Conservative PM candidate gate; not proof of calibrated energy or change reporting."""
    import math
    issues=[]
    endpoint=(device or {}).get('endpoints',{}).get('1',{})
    rows=endpoint.get('configured_reportings',[])
    configured=any(row.get('cluster') in ('haElectricalMeasurement',2820) and
                   row.get('attribute',row.get('attrId')) in ('activePower',1291) and
                   0 <= row.get('minimum_report_interval',row.get('minRepIntval',99999)) <= 10 and
                   0 < row.get('maximum_report_interval',row.get('maxRepIntval',99999)) <= 60 and
                   0 < row.get('reportable_change',row.get('repChange',99999)) <= 5 for row in rows)
    if not configured:issues.append('PM activePower max-60s reporting not configured')
    if not fresh_state:issues.append('No nonretained post-subscription PM MQTT state')
    else:
        for name,low,high in [('voltage',180,260),('current',0,32),('power',0,8000),('energy',0,1e9)]:
            try:value=float(fresh_state[name]);valid=math.isfinite(value) and low<=value<=high
            except (KeyError,TypeError,ValueError):valid=False
            if not valid:issues.append('Missing/unscaled/implausible PM '+name)
    return issues


def assess_retained_state(before,after,relay_key,require_pm):
    """Same-role OTA state gate; not proof of physical relay or metrology accuracy."""
    import math
    issues=[]
    if not isinstance(before,dict) or not isinstance(after,dict):
        return ['Missing fresh pre/post OTA state snapshots']
    if before.get(relay_key) not in ('ON','OFF'):
        issues.append('Preflash relay state missing')
    elif after.get(relay_key)!=before[relay_key]:
        issues.append('Relay state changed across same-role OTA')
    if before.get('relay_physical_mode') is not None and (
            after.get('relay_physical_mode')!=before['relay_physical_mode']):
        issues.append('Physical relay policy changed across same-role OTA')
    if require_pm:
        old=before.get('energy');new=after.get('energy')
        if (type(old) not in (int,float) or type(new) not in (int,float) or
                not math.isfinite(old) or not math.isfinite(new) or old<0 or new<0):
            issues.append('Missing/invalid cumulative PM energy before or after OTA')
        elif new + 0.02 < old:
            issues.append('Cumulative PM energy dropped across same-role OTA')
    return issues


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
    p.add_argument('--require-pm', action='store_true', help='Gate PM periodic reporting and plausible fresh standard MQTT values')
    p.add_argument('--preflash-lock', help='Same-role OTA lock with captured relay/energy baseline')
    p.add_argument('--expected-image-sha256', help='Required with --preflash-lock')
    p.add_argument('--relay-get-key',choices=['state','state_relay'],default='state')
    return p.parse_args()


def main():
    args = arguments()
    if bool(args.preflash_lock)!=bool(args.expected_image_sha256):
        raise ValueError('Baseline lock and exact OTA image SHA256 are required together')
    assert 4 <= args.observe_seconds <= 180, 'Observation window must be 4..180 seconds'
    # PM periodic reporting may legally take up to 60 s; allow a margin beyond that maximum.
    observation_seconds = max(args.observe_seconds, 75) if args.require_pm else args.observe_seconds
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
        time.sleep(observation_seconds)
    finally:
        client.loop_stop()
        client.disconnect()
    node=None; node_error=None
    target=next((d for d in (snapshot['inventory'] or []) if d.get('ieee_address')==args.ieee),None)
    if target and target.get('network_address') is not None:
        try: node=read_node_descriptor(args.mqtt_config,args.broker,args.ieee,target['network_address'])
        except (ValueError,TimeoutError,OSError) as error:node_error=repr(error)
    result, problems, target = evaluate(snapshot['inventory'], snapshot['state'], args.ieee,
                                         args.expect_role, args.expect_build, node)
    pm_issues = pm_readiness(target, snapshot['state']) if args.require_pm else []
    problems.extend(pm_issues)
    retention_issues=[]
    if args.preflash_lock:
        try:
            lock=json.loads(Path(args.preflash_lock).read_text(encoding='utf8'))
            validate_campaign(lock,args.ieee,args.device,args.expected_image_sha256)
            if lock.get('relay_get_key')!=args.relay_get_key:
                raise ValueError('Preflash relay endpoint differs from verification endpoint')
            retention_issues=assess_retained_state(
                lock.get('preflash_state'),snapshot['state'],args.relay_get_key,args.require_pm)
        except (OSError,ValueError,TypeError,KeyError) as error:
            retention_issues=[type(error).__name__+': '+str(error)[:180]]
        problems.extend(retention_issues)
    if snapshot['bridge'] != 'online': problems.append('Zigbee2MQTT bridge not verified online')
    if node_error: problems.append('live ZDO probe failed: '+node_error)
    if snapshot['errors']: problems.append('target-related Zigbee2MQTT errors observed')
    if problems: result = 'unconfirmed'
    evidence = {'observed_at': dt.datetime.now().astimezone().isoformat(),
                'target_name': args.device, 'target_ieee': args.ieee,
                'expected_role': args.expect_role, 'expected_build': args.expect_build,
                'result': result, 'issues': problems, 'bridge': snapshot['bridge'],
                'observation_seconds': observation_seconds,
                'pm_readiness': {'required': args.require_pm, 'issues': pm_issues},
                'same_role_retention': {'required': bool(args.preflash_lock),
                    'issues':retention_issues,'physical_acceptance':False},
                'live_node_descriptor': node, 'zdo_error': node_error,
                'cached_inventory_role_disagrees_with_live_zdo': bool(target and node and target.get('type') != node['role']),
                'fresh_mqtt_state_observed': snapshot['state'] is not None,
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
