"""Offline tests of group-membership snapshot/restore; no live MQTT."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_z2m_device_state as state

IEEE = '0xa4c1380000000001'
OTHER = '0xa4c1380000000002'


def reply(endpoint, frame, status='ok'):
    return {'status': status, 'data': {'clusterID': 4, 'endpoint': endpoint,
                                       'data': {'type': 'Buffer', 'data': frame}}}


def membership_frame(groups):
    return [0x19, 1, 0x02, 8, len(groups)] + [b for g in groups for b in (g & 0xFF, g >> 8)]


def test_parse_membership_reads_group_ids():
    assert state.parse_membership(reply(2, membership_frame([31, 2, 0x1234])), 2) == [2, 31, 0x1234]
    assert state.parse_membership(reply(2, membership_frame([])), 2) == []


def test_parse_membership_skips_manufacturer_code():
    frame = [0x1D, 0x41, 0x11, 7, 0x02, 8, 1, 5, 0]
    assert state.parse_membership(reply(1, frame), 1) == [5]


def test_default_response_means_no_groups_server_on_that_endpoint():
    with pytest.raises(state.GroupsUnsupported, match='0x81'):
        state.parse_membership(reply(1, [0x18, 1, 0x0B, 0x02, 0x81]), 1)


@pytest.mark.parametrize('response', [
    reply(2, membership_frame([31]), status='error'),
    reply(1, membership_frame([31])),            # wrong endpoint
    {'status': 'ok', 'data': {'clusterID': 6, 'endpoint': 2, 'data': {'data': [0x19, 1, 2, 8, 0]}}},
    reply(2, [0x19, 1, 0x02, 8, 2, 31, 0]),      # truncated list
    reply(2, [0x19, 1, 0x01, 8, 0]),             # not getMembership response
])
def test_parse_membership_rejects_unexpected_replies(response):
    with pytest.raises(ValueError):
        state.parse_membership(response, 2)


def test_relay_endpoint_follows_firmware_layout():
    assert state.default_relay_endpoint('_TZ3000_b28wrpvx', 'TS011F') == 1
    assert state.default_relay_endpoint('b28wrpvx', 'TS011F-BS-PM') == 2
    assert state.default_relay_endpoint('custom', 'TS011F-BS') == 2
    with pytest.raises(ValueError, match='explicitly'):
        state.default_relay_endpoint('iedhxgyi', 'TS0726-3-BS')


def test_restore_plan_unions_both_accounts_and_flags_unknown_groups():
    snap = {'z2m_membership': [{'id': 31, 'friendly_name': 'Shutdown', 'endpoint': 1}],
            'device_groups': [2, 77]}
    current = [{'id': 2, 'friendly_name': 'Supportive'}, {'id': 31, 'friendly_name': 'Shutdown'}]
    plan = state.restore_plan(snap, current, [2])
    assert plan == {'expected': [2, 31, 77], 'missing': [31, 77],
                    'restorable': [{'id': 31, 'friendly_name': 'Shutdown'}],
                    'unrestorable': [77]}


class FakeZ2M:
    def __init__(self, device_groups, *, add_takes_effect=True):
        self.inventory = [
            {'friendly_name': 'Socket', 'ieee_address': IEEE, 'network_address': 4660,
             'type': 'Router', 'software_build_id': '1.2.5-bseedr23'},
            {'friendly_name': 'Other', 'ieee_address': OTHER, 'network_address': 1}]
        self.groups = [
            {'id': 2, 'friendly_name': 'Supportive',
             'members': [{'ieee_address': IEEE, 'endpoint': 1}]},
            {'id': 31, 'friendly_name': 'Shutdown',
             'members': [{'ieee_address': IEEE, 'endpoint': 1},
                         {'ieee_address': OTHER, 'endpoint': 1}]},
            {'id': 40, 'friendly_name': 'Unrelated',
             'members': [{'ieee_address': OTHER, 'endpoint': 1}]}]
        self.device_groups = device_groups
        self.add_takes_effect = add_takes_effect
        self.requests = []

    def handle(self, path, body):
        if path == 'action':
            params = body['params']
            assert body['action'] == 'raw' and params['cluster_key'] == 'genGroups'
            assert params['zcl']['command_key'] == 'getMembership'
            assert params['ieee_address'] == IEEE
            ep = params['dst_endpoint']
            groups = self.device_groups.get(ep)
            frame = [0x18, 1, 0x0B, 0x02, 0x81] if groups is None else membership_frame(groups)
            return reply(ep, frame)
        if path == 'group/members/add':
            assert body['device'] == 'Socket'
            if self.add_takes_effect:
                gid = next(g['id'] for g in self.groups if g['friendly_name'] == body['group'])
                self.device_groups.setdefault(body['endpoint'], []).append(gid)
            return {'status': 'ok'}
        if path == 'device/configure':
            assert body['id'] == IEEE
            return {'status': 'ok'}
        raise AssertionError('Request outside the restore contract: ' + path)

    def client(self):
        fake = self

        class Client:
            def __init__(self, *args, **kwargs): pass
            def username_pw_set(self, *args): pass
            def connect(self, *args): pass
            def subscribe(self, *args): pass
            def loop_stop(self): pass
            def disconnect(self): pass

            def emit(self, topic, data, retain=False):
                self.on_message(self, None, SimpleNamespace(
                    topic='z2m/' + topic, payload=json.dumps(data).encode(), retain=retain))

            def loop_start(self):
                self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)
                self.emit('bridge/devices', fake.inventory, True)
                self.emit('bridge/groups', fake.groups, True)
                self.emit('bridge/info', {'permit_join': False}, True)

            def publish(self, topic, payload, qos=0):
                body = json.loads(payload)
                path = topic.split('/bridge/request/', 1)[1]
                fake.requests.append((path, body))
                answer = fake.handle(path, body)
                self.emit('bridge/response/' + path, dict(answer, transaction=body['transaction']))
                return SimpleNamespace(wait_for_publish=lambda *a: None, is_published=lambda: True)
        return Client


@pytest.fixture
def harness(tmp_path, monkeypatch):
    config = tmp_path / 'mqtt.yaml'
    config.write_text('mqtt: {base_topic: z2m}')
    monkeypatch.setattr(state.time, 'sleep', lambda *_: None)

    def install(fake):
        monkeypatch.setattr(state, 'mqtt', SimpleNamespace(
            Client=fake.client(), CallbackAPIVersion=SimpleNamespace(VERSION2=2)))
        return fake

    def argv(mode, output, *extra):
        return [mode, '--device', 'Socket', '--ieee', IEEE, '--mqtt-config', str(config),
                '--broker', 'mock', '--output', str(output), *extra]
    return SimpleNamespace(install=install, argv=argv, tmp=tmp_path)


def test_snapshot_records_z2m_and_stock_device_membership(harness):
    fake = harness.install(FakeZ2M({1: [2, 31]}))
    out = harness.tmp / 'snapshot.json'
    state.main(harness.argv('snapshot', out, '--relay-endpoint', '1'))
    snap = json.loads(out.read_text())
    assert snap['device_groups'] == [2, 31] and snap['device_error'] is None
    assert [r['id'] for r in snap['z2m_membership']] == [2, 31]
    assert {path for path, _ in fake.requests} == {'action'}


def test_snapshot_is_read_only_and_refuses_restore_options(harness):
    harness.install(FakeZ2M({1: []}))
    with pytest.raises(ValueError, match='read-only'):
        state.main(harness.argv('snapshot', harness.tmp / 's.json', '--relay-endpoint', '1',
                                '--configure'))


def _snapshot_file(harness, groups=(2, 31)):
    snap = harness.tmp / 'pre.json'
    snap.write_text(json.dumps({
        'kind': 'device_state_snapshot', 'ieee': IEEE, 'device_groups': list(groups),
        'z2m_membership': [{'id': 2, 'friendly_name': 'Supportive', 'endpoint': 1},
                           {'id': 31, 'friendly_name': 'Shutdown', 'endpoint': 1}]}))
    return snap


def test_restore_adds_missing_groups_on_new_relay_endpoint_and_verifies(harness):
    fake = harness.install(FakeZ2M({1: None, 2: [2]}))
    out = harness.tmp / 'restore.json'
    state.main(harness.argv('restore', out, '--relay-endpoint', '2', '--confirm-ieee', IEEE,
                            '--snapshot', str(_snapshot_file(harness)), '--configure'))
    body = json.loads(out.read_text())
    assert body['result'] == 'restored' and body['issues'] == []
    assert body['device_groups_before'] == [2] and body['device_groups_after'] == [2, 31]
    adds = [b for p, b in fake.requests if p == 'group/members/add']
    assert [(b['group'], b['endpoint']) for b in adds] == [('Shutdown', 2)]
    paths = [p for p, _ in fake.requests]
    assert paths[0] == 'device/configure' and paths.count('device/configure') == 1
    assert set(paths) <= {'device/configure', 'action', 'group/members/add'}


def test_restore_without_configure_flag_never_configures(harness):
    fake = harness.install(FakeZ2M({2: [2, 31]}))
    out = harness.tmp / 'restore.json'
    state.main(harness.argv('restore', out, '--relay-endpoint', '2', '--confirm-ieee', IEEE,
                            '--snapshot', str(_snapshot_file(harness))))
    assert json.loads(out.read_text())['result'] == 'restored'
    assert [p for p, _ in fake.requests] == ['action', 'action']


def test_restore_fails_closed_when_device_does_not_take_the_group(harness):
    harness.install(FakeZ2M({2: []}, add_takes_effect=False))
    out = harness.tmp / 'restore.json'
    with pytest.raises(SystemExit) as done:
        state.main(harness.argv('restore', out, '--relay-endpoint', '2', '--confirm-ieee', IEEE,
                                '--snapshot', str(_snapshot_file(harness))))
    assert done.value.code == 2
    body = json.loads(out.read_text())
    assert body['result'] == 'incomplete' and 'still missing groups [2, 31]' in body['issues'][0]


def test_restore_fails_closed_for_group_unknown_to_z2m(harness):
    harness.install(FakeZ2M({2: []}))
    out = harness.tmp / 'restore.json'
    with pytest.raises(SystemExit):
        state.main(harness.argv('restore', out, '--relay-endpoint', '2', '--confirm-ieee', IEEE,
                                '--snapshot', str(_snapshot_file(harness, groups=(2, 31, 77)))))
    body = json.loads(out.read_text())
    assert body['device_groups_after'] == [2, 31]
    assert any('77' in issue for issue in body['issues'])


def test_restore_requires_exact_confirmation_and_matching_snapshot(harness):
    harness.install(FakeZ2M({2: []}))
    snap = _snapshot_file(harness)
    with pytest.raises(ValueError, match='confirm'):
        state.main(harness.argv('restore', harness.tmp / 'a.json', '--relay-endpoint', '2',
                                '--confirm-ieee', OTHER, '--snapshot', str(snap)))
    other = harness.tmp / 'other.json'
    other.write_text(json.dumps({'kind': 'device_state_snapshot', 'ieee': OTHER}))
    with pytest.raises(ValueError, match='different device'):
        state.main(harness.argv('restore', harness.tmp / 'b.json', '--relay-endpoint', '2',
                                '--confirm-ieee', IEEE, '--snapshot', str(other)))


def test_evidence_must_be_new_and_outside_repository(harness):
    harness.install(FakeZ2M({1: []}))
    inside = Path(state.ROOT) / 'snapshot.json'
    with pytest.raises(ValueError, match='outside'):
        state.main(harness.argv('snapshot', inside, '--relay-endpoint', '1'))
    existing = harness.tmp / 'exists.json'
    existing.write_text('{}')
    with pytest.raises(ValueError, match='new file'):
        state.main(harness.argv('snapshot', existing, '--relay-endpoint', '1'))
