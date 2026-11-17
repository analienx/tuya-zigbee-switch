"""Snapshot and restore a BSEED socket's Zigbee group membership around an OTA.

A stock-to-custom conversion moves the relay, and the Groups cluster with it,
from endpoint 1 to endpoint 2. A Router<->Client role change erases the
socket's own group table and reporting setup. Zigbee2MQTT keeps listing the
old membership and still treats the device as configured, so group commands
silently miss the socket and reports stop.

snapshot  Read-only: the Zigbee2MQTT membership plus the groups the device
          itself reports on its relay endpoint.
restore   Optionally one target-only device/configure (reporting and
          coordinator bindings), then add the relay endpoint back to each
          snapshotted group it lacks, verified by asking the device again.
          Never removes membership and never sends On/Off or group commands.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import yaml

ROOT = Path(__file__).resolve().parents[1]
STOCK_PREFIXES = ('_TZ3000_', '_TZ3002_')
SOCKET_MODELS = ('TS011F', 'TS011F-BS-PM', 'TS011F-BS')
STOCK_RELAY_ENDPOINT = 1
CUSTOM_RELAY_ENDPOINT = 2  # custom firmware: switch endpoint 1, relay endpoint 2
MEMBERSHIP_ATTEMPTS = 3
MEMBERSHIP_RETRY_SECONDS = 4


class GroupsUnsupported(ValueError):
    """The endpoint answered, but has no Groups server (e.g. stock EP layout)."""


def default_relay_endpoint(manufacturer, model):
    if model not in SOCKET_MODELS:
        raise ValueError('Relay endpoint default exists only for single-relay BSEED '
                         'sockets; set the relay endpoint explicitly')
    if str(manufacturer).startswith(STOCK_PREFIXES):
        return STOCK_RELAY_ENDPOINT
    return CUSTOM_RELAY_ENDPOINT


def parse_membership(response, endpoint):
    """Group IDs from a Zigbee2MQTT raw-action reply to Groups getMembership."""
    if not isinstance(response, dict) or response.get('status') != 'ok':
        raise ValueError('Group membership request failed: ' +
                         str((response or {}).get('error'))[:200])
    data = response.get('data') or {}
    if data.get('clusterID') != 4 or data.get('endpoint') != endpoint:
        raise ValueError('Reply is not from the Groups cluster on endpoint %d' % endpoint)
    frame = (data.get('data') or {}).get('data')
    if not isinstance(frame, list) or not frame:
        raise ValueError('Missing ZCL reply frame')
    header = 5 if frame[0] & 0x04 else 3
    if len(frame) < header:
        raise ValueError('Truncated ZCL header')
    command, cluster_specific = frame[header - 1], (frame[0] & 0x03) == 0x01
    if not cluster_specific and command == 0x0B and len(frame) >= header + 2:
        raise GroupsUnsupported('Endpoint %d rejected getMembership, ZCL status 0x%02x'
                                % (endpoint, frame[header + 1]))
    if not cluster_specific or command != 0x02 or len(frame) < header + 2:
        raise ValueError('Unexpected ZCL reply to getMembership')
    count, body = frame[header + 1], frame[header + 2:]
    if len(body) < 2 * count:
        raise ValueError('Truncated group list')
    return sorted({body[2 * i] | (body[2 * i + 1] << 8) for i in range(count)})


def z2m_membership(groups, ieee):
    rows = []
    for group in groups or []:
        for member in group.get('members') or []:
            if member.get('ieee_address') == ieee:
                rows.append({'id': group.get('id'), 'friendly_name': group.get('friendly_name'),
                             'endpoint': member.get('endpoint')})
    return sorted(rows, key=lambda r: (r['id'], r['endpoint'] or 0))


def expected_groups(snapshot):
    """Everything the socket belonged to before the OTA, by either account."""
    ids = {row['id'] for row in snapshot.get('z2m_membership') or []}
    ids.update(snapshot.get('device_groups') or [])
    return sorted(ids)


def restore_plan(snapshot, current_groups, device_now):
    expected = expected_groups(snapshot)
    missing = [gid for gid in expected if gid not in (device_now or [])]
    known = {g.get('id'): g.get('friendly_name') for g in current_groups or []}
    return {'expected': expected, 'missing': missing,
            'restorable': [{'id': gid, 'friendly_name': known[gid]}
                           for gid in missing if gid in known],
            'unrestorable': [gid for gid in missing if gid not in known]}


def private_output(path):
    output = Path(path).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Evidence must be a new file outside the repository')
    return output


def target_entry(inventory, ieee, device):
    matches = [d for d in inventory or [] if d.get('ieee_address') == ieee]
    if len(matches) != 1 or matches[0].get('friendly_name') != device:
        raise ValueError('Exact target name/IEEE not found in Zigbee2MQTT inventory')
    if matches[0].get('network_address') is None:
        raise ValueError('Target has no network address')
    return matches[0]


class Bridge:
    """Transaction-matched Zigbee2MQTT bridge requests; nothing implicit."""

    def __init__(self, mqtt_config, broker):
        config = yaml.safe_load(Path(mqtt_config).read_text(encoding='utf8'))['mqtt']
        self.base = config.get('base_topic', 'zigbee2mqtt')
        self.broker = broker
        self.inventory = self.groups = self.info = None
        self.responses = {}
        self.ready, self.wake = threading.Event(), threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='bseed-state-' + uuid.uuid4().hex)
        self.client.username_pw_set(config.get('user', ''), config.get('password', ''))
        self.client.on_connect, self.client.on_message = self.on_connect, self.on_message

    def on_connect(self, client, _u, _flags, reason, _properties):
        if reason.is_failure:
            return
        client.subscribe([(self.base + '/bridge/' + t, 1)
                          for t in ('devices', 'groups', 'info', 'response/#')])
        self.ready.set()

    def on_message(self, _c, _u, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        topic = message.topic[len(self.base) + 1:]
        if topic == 'bridge/devices' and isinstance(data, list):
            self.inventory = data
        elif topic == 'bridge/groups' and isinstance(data, list):
            self.groups = data
        elif topic == 'bridge/info' and isinstance(data, dict):
            self.info = data
        elif topic.startswith('bridge/response/') and isinstance(data, dict):
            if isinstance(data.get('transaction'), str):
                self.responses[data['transaction']] = data
        self.wake.set()

    def wait_for(self, predicate, seconds, error):
        deadline = time.monotonic() + seconds
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(error)
            self.wake.wait(min(0.3, remaining))
            self.wake.clear()

    def start(self):
        self.client.connect(self.broker, 1883, 10)
        self.client.loop_start()
        if not self.ready.wait(10):
            raise TimeoutError('MQTT subscription not ready')
        self.wait_for(lambda: None not in (self.inventory, self.groups, self.info), 12,
                      'Bridge devices/groups/info not received')

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def request(self, path, payload, timeout):
        token = 'bseed-state-' + uuid.uuid4().hex
        sent = self.client.publish(self.base + '/bridge/request/' + path,
                                   json.dumps(dict(payload, transaction=token)), qos=1)
        sent.wait_for_publish(5)
        if not sent.is_published():
            raise TimeoutError('Request publish not confirmed: ' + path)
        self.wait_for(lambda: token in self.responses, timeout, 'No reply to ' + path)
        return self.responses.pop(token)

    def membership(self, target, endpoint):
        reply = self.request('action', {'action': 'raw', 'params': {
            'ieee_address': target['ieee_address'],
            'network_address': target['network_address'],
            'dst_endpoint': endpoint, 'profile_id': 0x0104, 'cluster_key': 'genGroups',
            'zcl': {'frame_type': 1, 'direction': 0, 'command_key': 'getMembership',
                    'payload': {'groupcount': 0, 'grouplist': []}},
            'disable_response': False}}, 25)
        return parse_membership(reply, endpoint)


def membership_with_retry(bridge, target, endpoint):
    error = None
    for attempt in range(MEMBERSHIP_ATTEMPTS):
        if attempt:
            time.sleep(MEMBERSHIP_RETRY_SECONDS)
        try:
            return bridge.membership(target, endpoint), None
        except GroupsUnsupported as unsupported:
            return None, str(unsupported)
        except (ValueError, TimeoutError) as failure:
            error = failure
    raise TimeoutError('Device did not report group membership: ' + repr(error))


def snapshot(a, bridge):
    target = target_entry(bridge.inventory, a.ieee, a.device)
    groups, device_error = membership_with_retry(bridge, target, a.relay_endpoint)
    return {'kind': 'device_state_snapshot', 'observed_at': dt.datetime.now().astimezone().isoformat(),
            'device': a.device, 'ieee': a.ieee, 'relay_endpoint': a.relay_endpoint,
            'software_build_id': target.get('software_build_id'), 'type': target.get('type'),
            'z2m_membership': z2m_membership(bridge.groups, a.ieee),
            'device_groups': groups, 'device_error': device_error}


def restore(a, bridge, snap):
    if snap.get('kind') != 'device_state_snapshot' or snap.get('ieee') != a.ieee:
        raise ValueError('Snapshot belongs to a different device')
    target = target_entry(bridge.inventory, a.ieee, a.device)
    result = {'kind': 'device_state_restore', 'device': a.device, 'ieee': a.ieee,
              'relay_endpoint': a.relay_endpoint, 'snapshot': str(a.snapshot),
              'configure': None, 'requests': [], 'issues': []}
    if a.configure:
        reply = bridge.request('device/configure', {'id': a.ieee}, 60)
        result['configure'] = {'status': reply.get('status'), 'error': reply.get('error')}
        if reply.get('status') != 'ok':
            result['issues'].append('device/configure failed: ' + str(reply.get('error'))[:200])
    before, error = membership_with_retry(bridge, target, a.relay_endpoint)
    if before is None:
        raise ValueError('Relay endpoint has no Groups server after the OTA: ' + error)
    plan = restore_plan(snap, bridge.groups, before)
    result.update(device_groups_before=before, plan=plan)
    for group in plan['restorable']:
        reply = bridge.request('group/members/add', {
            'group': group['friendly_name'], 'device': a.device,
            'endpoint': a.relay_endpoint}, 40)
        result['requests'].append({'group': group, 'status': reply.get('status'),
                                   'error': reply.get('error')})
    for gid in plan['unrestorable']:
        result['issues'].append('group %s is unknown to Zigbee2MQTT and cannot be re-added' % gid)
    after, _ = membership_with_retry(bridge, target, a.relay_endpoint)
    result['device_groups_after'] = after
    lost = [gid for gid in plan['expected'] if gid not in (after or [])]
    if lost:
        result['issues'].append('device is still missing groups %s' % lost)
    result['result'] = 'restored' if not result['issues'] else 'incomplete'
    return result


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['snapshot', 'restore'])
    for name in ('device', 'ieee', 'mqtt-config', 'broker', 'output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--relay-endpoint', type=int, required=True)
    p.add_argument('--confirm-ieee', help='restore only: exact target IEEE')
    p.add_argument('--snapshot', help='restore only: snapshot evidence from before the OTA')
    p.add_argument('--configure', action='store_true',
                   help='restore only: one target-only device/configure first')
    return p.parse_args(argv)


def main(argv=None):
    a = arguments(argv)
    if not 1 <= a.relay_endpoint <= 240:
        raise ValueError('Relay endpoint must be 1..240')
    output = private_output(a.output)
    snap = None
    if a.mode == 'restore':
        if a.confirm_ieee != a.ieee:
            raise ValueError('Restore refused: confirm the exact target IEEE')
        if not a.snapshot:
            raise ValueError('Restore requires --snapshot')
        source = Path(a.snapshot).expanduser().resolve()
        if source.is_relative_to(ROOT):
            raise ValueError('Snapshot must be private evidence outside the repository')
        snap = json.loads(source.read_text(encoding='utf8'))
    elif a.confirm_ieee or a.configure or a.snapshot:
        raise ValueError('Snapshot is read-only and takes no confirmation or restore options')
    bridge = Bridge(a.mqtt_config, a.broker)
    bridge.start()
    try:
        body = snapshot(a, bridge) if a.mode == 'snapshot' else restore(a, bridge, snap)
    finally:
        bridge.stop()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(body, indent=2, default=str), encoding='utf8')
    print('PRIVATE_STATE_EVIDENCE', output, flush=True)
    if a.mode == 'snapshot':
        print('SNAPSHOT', json.dumps({'z2m': body['z2m_membership'],
                                      'device': body['device_groups']}), flush=True)
        return
    print('RESTORE', body['result'], json.dumps(body['issues']), flush=True)
    if body['result'] != 'restored':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
