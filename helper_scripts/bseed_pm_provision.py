"""Target-only PM reporting audit and safe provisioning; dry-run by default."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import threading
import time
import uuid
import paramiko
import paho.mqtt.client as mqtt
import yaml
from bseed_zdo_live import read_node_descriptor

ROOT = Path(__file__).resolve().parents[1]
SCALES = {'haElectricalMeasurement': {'acVoltageMultiplier': 1, 'acVoltageDivisor': 100,
    'acCurrentMultiplier': 1, 'acCurrentDivisor': 1000, 'acPowerMultiplier': 1, 'acPowerDivisor': 1},
    'seMetering': {'multiplier': 1, 'divisor': 1000}}
REPORTS = [('haElectricalMeasurement', 'activePower', 1291, 10, 60, 5),
           ('haElectricalMeasurement', 'rmsCurrent', 1288, 5, 300, 50),
           ('haElectricalMeasurement', 'rmsVoltage', 1285, 5, 300, 5),
           ('seMetering', 'currentSummDelivered', 0, 10, 600, 1)]
CLUSTER_IDS = {'haElectricalMeasurement': 2820, 'seMetering': 1794}


def select_target(inventory, ieee, name, role, build):
    matches = [d for d in (inventory or []) if d.get('ieee_address') == ieee]
    if len(matches) != 1: raise ValueError('Target IEEE absent or ambiguous')
    d = matches[0]
    if (d.get('friendly_name'), d.get('type'), d.get('software_build_id'),
        d.get('interview_completed')) != (name, role, build, True):
        raise ValueError('Target name, role, custom build or interview mismatch')
    ep = d.get('endpoints') or {}
    if '1' not in ep or '2' not in ep: raise ValueError('PM or relay endpoint missing')
    for cluster in CLUSTER_IDS:
        inputs = ep['1'].get('clusters', {}).get('input', [])
        if cluster not in inputs and CLUSTER_IDS[cluster] not in inputs:
            raise ValueError('PM input cluster missing: ' + cluster)
    return d


def missing_reports(device):
    rows = device['endpoints']['1'].get('configured_reportings', [])
    missing = []
    for cluster, attr, attr_id, min_sec, max_sec, change in REPORTS:
        good = any(r.get('cluster') in (cluster, CLUSTER_IDS[cluster]) and
            r.get('attribute', r.get('attrId')) in (attr, attr_id) and
            0 <= r.get('minimum_report_interval', r.get('minRepIntval', 65535)) <= min_sec and
            0 < r.get('maximum_report_interval', r.get('maxRepIntval', 65535)) <= max_sec and
            0 <= r.get('reportable_change', r.get('repChange', 65535)) <= change for r in rows)
        if not good: missing.append((cluster, attr, attr_id, min_sec, max_sec, change))
    return missing


def inspect_db(record, ieee, build, coordinator_ieee):
    if record.get('ieeeAddr') != ieee or record.get('swBuildId') != build:
        raise ValueError('Database identity/build differs from inventory')
    ep = record.get('endpoints', {}).get('1', {})
    if not ep: raise ValueError('Endpoint 1 absent from persisted device record')
    for cluster, expected in SCALES.items():
        attrs = ep.get('clusters', {}).get(cluster, {}).get('attributes', {})
        for key, required in expected.items():
            val = attrs.get(key)
            if type(val) is not int or val != required:
                raise ValueError('Missing/unrecognized persisted device scale: ' + cluster + '.' + key)
    bound = {b.get('cluster') for b in ep.get('binds', [])
             if b.get('type') == 'endpoint' and
             b.get('deviceIeeeAddress') == coordinator_ieee and b.get('endpointID') == 1}
    if not {2820, 1794}.issubset(bound):
        raise ValueError('Missing endpoint-1 PM bindings to verified coordinator IEEE')
    return {'cached_scales_verified': True, 'metering_bindings_verified': True}


def pm_snapshot(record):
    """Private, target-only reporting/binding/attribute evidence; no credentials."""
    ep = record.get('endpoints', {}).get('1', {})
    return {'network_address': record.get('nwkAddr'),
            'reportings': ep.get('configuredReportings', []),
            'bindings': ep.get('binds', []),
            'attributes': {k: ep.get('clusters', {}).get(k, {}).get('attributes', {})
                           for k in SCALES}}


def read_db(host, username, key, path, ieee):
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys(str(Path.home() / '.ssh' / 'known_hosts'))
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(host, username=username, key_filename=str(key), timeout=7,
                auth_timeout=7, banner_timeout=7)
    try:
        sftp = ssh.open_sftp()
        try:
            with sftp.open(path, 'r') as f:
                matches = [x for line in f if (x := json.loads(line.strip()))
                           .get('ieeeAddr') == ieee]
        finally: sftp.close()
    finally: ssh.close()
    if len(matches) != 1: raise ValueError('Exact IEEE not unique in read-only database')
    return matches[0]


class Bridge:
    def __init__(self, config, broker, name):
        self.base = config.get('base_topic', 'zigbee2mqtt')
        self.broker, self.name = broker, name
        self.ready, self.wake = threading.Event(), threading.Event()
        self.inventory, self.info, self.state = None, None, None
        self.states, self.errors, self.responses = [], [], {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='bseed-pm-' + uuid.uuid4().hex)
        self.client.username_pw_set(config.get('user', ''), config.get('password', ''))
        self.client.on_connect, self.client.on_message = self.on_connect, self.on_message

    def on_connect(self, c, _u, _flags, reason, _properties):
        if reason.is_failure: return
        topics = [self.base + '/bridge/' + t for t in ('devices', 'info', 'state', 'logging')]
        topics += [self.base + '/bridge/response/device/' + t for t in
                   ('configure', 'reporting/configure', 'reporting/read')]
        topics += [self.base + '/' + self.name]
        c.subscribe([(t, 1) for t in topics]); self.ready.set()

    def on_message(self, _c, _u, m):
        try: data = json.loads(m.payload)
        except (ValueError, UnicodeDecodeError): return
        topic = m.topic[len(self.base) + 1:]
        if topic == 'bridge/devices' and isinstance(data, list): self.inventory = data
        elif topic == 'bridge/info' and isinstance(data, dict): self.info = data
        elif topic == 'bridge/state': self.state = data.get('state') if isinstance(data, dict) else data
        elif topic == self.name and not m.retain and isinstance(data, dict):
            self.states.append((time.monotonic(), data)); self.states = self.states[-100:]
        elif topic.startswith('bridge/response/device/') and isinstance(data, dict):
            if isinstance(data.get('transaction'), str): self.responses[data['transaction']] = data
        elif topic == 'bridge/logging' and isinstance(data, dict):
            msg = str(data.get('message', ''))
            if self.name in msg and any(x in msg.lower() for x in ('error', 'timeout', 'failed')):
                self.errors.append(msg[:240])
        self.wake.set()

    def start(self):
        self.client.connect(self.broker, 1883, 10)
        self.client.loop_start()
        if not self.ready.wait(10): raise TimeoutError('MQTT subscribe failed')
        self.wait_for(lambda: self.inventory is not None and self.info is not None
                      and self.state is not None, 12, 'Bridge inventory/info/state absent')

    def stop(self):
        self.client.loop_stop(); self.client.disconnect()

    def wait_for(self, predicate, seconds, error):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate(): return
            self.wake.wait(min(0.3, max(0, deadline - time.monotonic())))
            self.wake.clear()
        if not predicate(): raise TimeoutError(error)

    def request(self, operation, payload, timeout=28):
        token = 'bseed-pm-' + uuid.uuid4().hex
        payload = dict(payload, transaction=token)
        topic = self.base + '/bridge/request/device/' + operation
        message = self.client.publish(topic, json.dumps(payload), qos=1)
        message.wait_for_publish(5)
        if not message.is_published(): raise TimeoutError('Request publish not confirmed')
        self.wait_for(lambda: token in self.responses, timeout,
                      'Target request response timed out: ' + operation)
        response = self.responses.pop(token)
        if response.get('status') != 'ok':
            raise RuntimeError('Target request returned failure: ' + operation + ' ' +
                               str(response.get('error', 'unknown'))[:180])
        return {'operation': operation, 'status': 'ok'}


def validate_states(states, db, idle=False):
    if len(states) < 2 or states[-1][0] - states[0][0] < 8:
        raise ValueError('Need at least two separated, fresh nonretained PM messages')
    ep = db['endpoints']['1']['clusters']
    electrical = ep['haElectricalMeasurement']['attributes']
    energy = ep['seMetering']['attributes']
    last = states[-1][1]
    expected = {'voltage': electrical['rmsVoltage'] / 100,
                'current': electrical['rmsCurrent'] / 1000,
                'power': electrical['activePower'],
                'energy': energy['currentSummDelivered'] / 1000}
    tolerance = {'voltage': 10, 'current': .15, 'power': 5, 'energy': .02}
    for metric, raw in expected.items():
        v = last.get(metric)
        if type(v) not in (float, int) or not math.isfinite(v):
            raise ValueError('Non-numeric or absent PM property: ' + metric)
        if v < 0 or (metric == 'voltage' and not 180 <= v <= 260):
            raise ValueError('Implausible PM property: ' + metric)
        if abs(v - raw) > tolerance[metric]:
            raise ValueError('Stale or inconsistent raw-to-MQTT PM scaling: ' + metric)
    if idle and (last['power'] != 0 or last['current'] != 0):
        raise ValueError('User-confirmed no-load still reports nonzero power/current')
    if any(states[i][1].get('energy', -1) > states[i+1][1].get('energy', -1) + .02
           for i in range(len(states) - 1)):
        raise ValueError('Fresh cumulative energy decreased or missing')
    return {'mqtt_samples': len(states), 'sample_span_seconds': round(states[-1][0]-states[0][0], 1),
            'fresh_scaled_pm': True, 'idle_zero_confirmed': bool(idle)}


def observation_policy(requested_seconds, expect_idle):
    """An idle gate needs a full max-300s current-report window plus margin."""
    if not 75 <= requested_seconds <= 420:
        raise ValueError('Observation seconds must be bounded to 75..420')
    return max(requested_seconds, 330) if expect_idle else requested_seconds, (310 if expect_idle else 8)


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('device', 'ieee', 'expect-role', 'expect-build', 'mqtt-config',
                'broker', 'ssh-host', 'ssh-key', 'output'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--ssh-user', default='root')
    p.add_argument('--database', default='/config/zigbee2mqtt/database.db')
    p.add_argument('--confirm-ieee', help='Required for --apply; must equal target exactly')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--allow-configure', action='store_true',
                   help='Allow one bounded device/configure if persisted scales or binds are missing')
    p.add_argument('--expect-idle', action='store_true')
    p.add_argument('--max-writes', type=int, default=1)
    p.add_argument('--observe-seconds', type=int, default=135)
    return p.parse_args()


def main():
    a = arguments()
    if not a.ieee.startswith('0x') or len(a.ieee) != 18:
        raise ValueError('Full exact IEEE required')
    if a.expect_role != 'EndDevice':
        raise ValueError('This provisioner implements Client-only scale semantics; Router must use independent role audit')
    if a.apply and a.confirm_ieee != a.ieee:
        raise ValueError('Explicit exact IEEE confirmation required for reporting writes')
    if not 1 <= a.max_writes <= 4: raise ValueError('Bounded reporting writes required')
    observe_seconds, minimum_span = observation_policy(a.observe_seconds, a.expect_idle)
    output = Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Evidence must be new and outside repository')
    config = yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    bridge = Bridge(config, a.broker, a.device)
    evidence = {'at': dt.datetime.now().astimezone().isoformat(),
                'target': a.device, 'ieee': a.ieee, 'mode': 'apply' if a.apply else 'audit',
                'result': 'unconfirmed', 'requests': [], 'attempted': [], 'issues': []}
    started = False
    try:
        bridge.start(); started = True
        if bridge.state != 'online' or bridge.info.get('permit_join') is not False:
            raise ValueError('Bridge offline or joining enabled: do not provision')
        device = select_target(bridge.inventory, a.ieee, a.device, a.expect_role, a.expect_build)
        if (device.get('model_id') != 'TS011F-BS-PM' or
            (device.get('definition') or {}).get('model') != 'TS011F_plug_1_2'):
            raise ValueError('Not the expected custom PM socket converter model')
        coord = bridge.info.get('coordinator', {}).get('ieee_address')
        if not coord: raise ValueError('Coordinator IEEE unavailable')
        node = read_node_descriptor(a.mqtt_config, a.broker, a.ieee, device['network_address'])
        if node.get('role') != a.expect_role:
            raise ValueError('Live ZDO role differs from expected role')
        db = read_db(a.ssh_host, a.ssh_user, a.ssh_key, a.database, a.ieee)
        evidence['before'] = pm_snapshot(db)
        try:
            inspect_db(db, a.ieee, a.expect_build, coord)
        except ValueError as error:
            if (not a.apply or not a.allow_configure or not
                str(error).startswith(('Missing/unrecognized persisted device scale:',
                                       'Missing endpoint-1 PM bindings'))):
                raise
            evidence['requests'].append(bridge.request('configure', {'id': a.ieee}, timeout=45))
            time.sleep(3)
            db = read_db(a.ssh_host, a.ssh_user, a.ssh_key, a.database, a.ieee)
            inspect_db(db, a.ieee, a.expect_build, coord)
        evidence['preflight'] = {'db_cached_scales': True, 'db_bindings': True,
                                 'live_zdo_role': node['role']}
        missing = missing_reports(device)
        evidence['missing_before'] = [x[1] for x in missing]
        if a.apply:
            for cluster, attr, attr_id, min_s, max_s, change in missing[:a.max_writes]:
                evidence['attempted'].append(attr)
                evidence['requests'].append(bridge.request('reporting/configure',
                    {'id': a.ieee, 'endpoint': 1, 'cluster': cluster, 'attribute': attr,
                     'minimum_report_interval': min_s, 'maximum_report_interval': max_s,
                     'reportable_change': change}))
                time.sleep(2)
        # Read back actual device reporting for the critical activePower path.
        if a.apply and (not missing or any(x[1] == 'activePower' for x in missing[:a.max_writes])):
            evidence['requests'].append(bridge.request('reporting/read',
                {'id': a.ieee, 'endpoint': 1, 'cluster': 'haElectricalMeasurement',
                 'configs': [{'attribute': 'activePower'}]}))
        updated = read_db(a.ssh_host, a.ssh_user, a.ssh_key, a.database, a.ieee)
        evidence['after'] = pm_snapshot(updated)
        inspect_db(updated, a.ieee, a.expect_build, coord)
        persisted = dict(device, endpoints={'1': {'configured_reportings':
                         updated['endpoints']['1'].get('configuredReportings', [])}})
        outstanding = missing_reports(persisted)
        evidence['missing_after'] = [x[1] for x in outstanding]
        if outstanding:
            raise ValueError('Missing PM report settings after bounded provisioning: ' +
                             ', '.join(x[1] for x in outstanding))
        if a.apply:
            baseline = time.monotonic()
            evidence['observation_policy'] = {'deadline_seconds': observe_seconds,
                                              'minimum_span_seconds': minimum_span}
            bridge.wait_for(lambda: (len([x for x in bridge.states if x[0] >= baseline]) >= 2
                            and bridge.states[-1][0] - next(x[0] for x in bridge.states if x[0] >= baseline) >= minimum_span),
                            observe_seconds, 'Insufficient passive PM telemetry over required interval')
            observed = [x for x in bridge.states if x[0] >= baseline]
            evidence['passive_mqtt'] = [{'offset_seconds': round(t - baseline, 1),
                    'metrics': {k: d.get(k) for k in ('power', 'current', 'voltage',
                                                      'energy', 'state_relay', 'linkquality')}}
                    for t, d in observed[-12:]]
            evidence['telemetry'] = validate_states(observed, updated, a.expect_idle)
        evidence['result'] = 'pm_configured_candidate' if a.apply else 'pm_configuration_audit_pass'
    except Exception as exc:
        evidence['issues'].append(type(exc).__name__ + ': ' + str(exc)[:250])
    finally:
        if started: bridge.stop()
        evidence['target_log_errors'] = bridge.errors[-15:]
        if bridge.errors and evidence['result'] != 'unconfirmed':
            evidence['result'] = 'unconfirmed'
            evidence['issues'].append('Target Z2M errors observed during provisioning')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2), encoding='utf8')
        print(json.dumps(evidence, indent=2), flush=True)
    if evidence['result'] == 'unconfirmed': raise SystemExit(2)
    print('Candidate only: physical load-to-zero and stability gates remain separate.', flush=True)


if __name__ == '__main__': main()
