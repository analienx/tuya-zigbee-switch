"""Read-only post-OTA evidence collector. Transfer success is never device acceptance.

This tool does not pair, reboot, toggle, interview, or update any Zigbee device.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import threading
import time

import paho.mqtt.client as mqtt
import yaml
from bseed_zdo_live import read_node_descriptor
from bseed_z2m_postota_reinterview import validate_campaign
from bseed_z2m_state_refresh import (
    PM_KEYS, PM_STAMP, add_refresh_arguments, collect as collect_state_refresh, fresh_pm_sample,
)


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
    p.add_argument('--skip-state-refresh', action='store_true',
                   help='skip the standard post-update read of relay/PM values (not recommended)')
    p.add_argument('--pm-audit-seconds', type=int,
                   help='also run the Router PM audit in this same observation window')
    from bseed_pm_role_audit import add_ssh_arguments
    add_ssh_arguments(p)
    add_refresh_arguments(p, standalone=False)
    return p.parse_args()


def observed_power(state, ieee, previous_stamp, started_ms, received_ms):
    """A distinct device-topic publication must contain a new converter power stamp."""
    if (state.get('device') or {}).get('ieeeAddr') not in (None, ieee):
        return None
    return fresh_pm_sample(state, baseline_ms=max(previous_stamp, started_ms - 1),
                           request_ms=started_ms, received_ms=received_ms)


def router_audit_observations(samples, refresh):
    """Use the existing refresh as a second sample when idle reporting only supplied one."""
    if len(samples) != 1 or not refresh or refresh['issues']:
        return samples
    power = refresh.get('freshPmSample')
    received, state = samples[0]
    if not power or power['sample_time_ms'] <= state[PM_STAMP]:
        return samples
    fields = refresh['fields']
    values = {key: fields[key]['fresh'] for key in PM_KEYS}
    values['power'] = power['power']
    elapsed = (power['sample_time_ms'] - state[PM_STAMP]) / 1000
    return [*samples, (received + elapsed, values)]


def main():
    args = arguments()
    if bool(args.preflash_lock)!=bool(args.expected_image_sha256):
        raise ValueError('Baseline lock and exact OTA image SHA256 are required together')
    if not (4 <= args.observe_seconds <= 180):
        raise AssertionError('Observation window must be 4..180 seconds')
    shared_audit = args.pm_audit_seconds is not None
    if shared_audit and (not args.require_pm or args.expect_role != 'Router' or
                         not (60 <= args.pm_audit_seconds <= 420) or
                         not args.ssh_host or not args.ssh_key):
        raise ValueError('Shared PM audit requires a Router PM profile, private SSH settings and 60..420 seconds')
    # PM periodic reporting may legally take up to 60 s; allow a margin beyond that maximum.
    observation_seconds = max(args.observe_seconds, 75) if args.require_pm else args.observe_seconds
    if shared_audit:
        observation_seconds = max(observation_seconds, args.pm_audit_seconds)
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    device_topic = base + '/' + args.device
    snapshot = {'inventory': None, 'info': None, 'state': None, 'errors': [], 'bridge': None}
    samples = []
    previous_stamp = 0
    ready = threading.Event()
    inventory_ready = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    started_ms = time.time() * 1000

    def on_connect(c, _userdata, _flags, reason, _properties):
        if reason.is_failure: return
        c.subscribe([(base + '/bridge/devices', 1), (base + '/bridge/state', 1),
                     (base + '/bridge/info', 1), (base + '/bridge/logging', 0),
                     (device_topic, 1)])
        ready.set()

    def on_message(_c, _userdata, message):
        nonlocal previous_stamp
        try: data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError): return
        if message.topic == base + '/bridge/devices' and isinstance(data, list):
            snapshot['inventory'] = data
        elif message.topic == base + '/bridge/info' and isinstance(data, dict):
            snapshot['info'] = data
        elif message.topic == base + '/bridge/state':
            snapshot['bridge'] = data.get('state') if isinstance(data, dict) else data
        if snapshot['inventory'] is not None and snapshot['bridge'] is not None and (
                not shared_audit or snapshot['info'] is not None):
            inventory_ready.set()
        if message.topic == device_topic and message.retain and isinstance(data, dict):
            retained_stamp = data.get(PM_STAMP)
            if (type(retained_stamp) in (float, int) and math.isfinite(retained_stamp) and
                    retained_stamp > previous_stamp):
                previous_stamp = retained_stamp
        elif message.topic == device_topic and not message.retain and isinstance(data, dict):
            if (data.get('device') or {}).get('ieeeAddr') not in (None, args.ieee):
                snapshot['errors'].append('state event belongs to another IEEE')
                return
            snapshot['state'] = data
            power = observed_power(data, args.ieee, previous_stamp, started_ms, time.time() * 1000)
            if power is not None:
                previous_stamp = power['sample_time_ms']
                samples.append((time.monotonic(), {**data, 'power': power['power']}))
        elif message.topic == base + '/bridge/logging' and isinstance(data, dict):
            line = str(data.get('message', ''))
            if args.device in line and any(word in line.lower() for word in ('failed', 'error', 'timeout', 'interview')):
                snapshot['errors'].append(line[:500])

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, 1883, 10)
    client.loop_start()
    node = None
    node_error = None
    audit = None
    try:
        if not (ready.wait(10)):
            raise AssertionError('MQTT connection/subscription failed')
        deadline = time.monotonic() + observation_seconds
        inventory_ready.wait(min(12, observation_seconds))
        target = next((d for d in (snapshot['inventory'] or [])
                       if d.get('ieee_address') == args.ieee and d.get('friendly_name') == args.device), None)
        if target and target.get('network_address') is not None:
            try:
                node = read_node_descriptor(args.mqtt_config, args.broker, args.ieee, target['network_address'])
            except (ValueError, TimeoutError, OSError) as error:
                node_error = repr(error)
        if target and not evaluate(snapshot['inventory'], None, args.ieee, args.expect_role,
                                   args.expect_build, node)[1] and snapshot['bridge'] == 'online':
            print('FIRMWARE_VERIFIED', args.device, args.expect_build, args.expect_role, flush=True)
        if shared_audit:
            from bseed_pm_role_audit import audit_configuration, audit_samples
            audit = {'result': 'unconfirmed', 'issues': [], 'writes': 0, 'physical_acceptance': False}
            try:
                attrs = audit_configuration(args, snapshot['inventory'], snapshot['info'], node, audit)
            except Exception as error:
                audit['issues'].append(f'{type(error).__name__}: {str(error)[:260]}')
        # Same-role non-PM sockets can be completely quiet after OTA. The
        # retention gate must not depend on unsolicited telemetry: request the
        # pinned read-only relay/policy state after subscriptions are active.
        if args.preflash_lock and not args.require_pm:
            client.publish(device_topic + '/get', json.dumps({
                args.relay_get_key: '', 'relay_physical_mode': ''
            }), qos=1, retain=False).wait_for_publish(5)
        time.sleep(max(0, deadline - time.monotonic()))
    finally:
        client.loop_stop()
        client.disconnect()
    result, problems, target = evaluate(snapshot['inventory'], snapshot['state'], args.ieee,
                                         args.expect_role, args.expect_build, node)
    if target and target.get('friendly_name') != args.device:
        problems.append('exact IEEE/friendly name mismatch')
    if snapshot['bridge'] != 'online': problems.append('Zigbee2MQTT bridge not verified online')
    if node_error: problems.append('live ZDO probe failed: '+node_error)
    software_verified = not problems
    latest_pm = samples[-1][1] if samples else None
    pm_issues = pm_readiness(target, latest_pm) if args.require_pm else []
    if args.require_pm and not samples:
        pm_issues.append('No newly stamped power measurement during the observation window')
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
    # Standard post-update step: replace cached pre-update values with fresh
    # device reads, so stale relay/power/energy numbers never pass as current.
    refresh = None
    if not args.skip_state_refresh:
        try:
            refresh = collect_state_refresh(args)
        except (ValueError, TimeoutError, OSError, KeyError, ImportError) as error:
            refresh = {'result': 'failed', 'issues': [type(error).__name__ + ': ' + str(error)[:180]]}
        problems.extend('state refresh: ' + issue for issue in refresh['issues'])
    if audit is not None:
        if not audit['issues']:
            observations = router_audit_observations(samples, refresh)
            audit['state_refresh_sample_used'] = len(observations) > len(samples)
            try:
                audit_samples(observations, attrs, args.expect_role, snapshot['errors'], audit)
            except ValueError as error:
                audit['issues'].append(str(error))
        pm_issues.extend('Router PM audit: ' + issue for issue in audit['issues'])
    problems.extend(pm_issues)
    if snapshot['errors']: problems.append('target-related Zigbee2MQTT errors observed')
    if problems: result = 'unconfirmed'
    evidence = {'observed_at': dt.datetime.now().astimezone().isoformat(),
                'target_name': args.device, 'target_ieee': args.ieee,
                'expected_role': args.expect_role, 'expected_build': args.expect_build,
                'result': result, 'software_verified': software_verified,
                'issues': problems, 'bridge': snapshot['bridge'],
                'observation_seconds': observation_seconds,
                'pm_readiness': {'required': args.require_pm, 'issues': pm_issues,
                                 'distinct_power_samples_observed': len(samples),
                                 'runner_read_during_observation': False if args.require_pm else None,
                                 'raw_zcl_report_proven': False},
                'pm_role_audit': audit,
                'same_role_retention': {'required': bool(args.preflash_lock),
                    'issues':retention_issues,'physical_acceptance':False},
                'state_refresh': refresh,
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
    if not (not outfile.exists()):
        raise AssertionError('Refuse to overwrite previous evidence')
    outfile.write_text(json.dumps(evidence, indent=2, default=str), encoding='utf8')
    print(json.dumps(evidence, indent=2, default=str), flush=True)
    if software_verified and pm_issues:
        print('FIRMWARE_VERIFIED_PM_UNCONFIRMED', args.device, flush=True)
    print('Physical relay, appliance safety, parent/rejoin, metrology and retained bindings '
          'require separate acceptance; candidate does NOT mean final flash acceptance.', flush=True)
    if result != 'postflash_candidate': raise SystemExit(2)


if __name__ == '__main__':
    main()
