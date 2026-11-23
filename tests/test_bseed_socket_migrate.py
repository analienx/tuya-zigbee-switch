"""One-command socket migration planning. GitHub-hosted CI only; no network."""
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_socket_migrate as mig  # noqa: E402
import bseed_ota_campaign as campaign  # noqa: E402
import bseed_relay_auto_off as relay  # noqa: E402
from bseed_zdo_live import parse_neighbor_page  # noqa: E402

IEEE = '0xa4c1380011223344'
PACKAGE = {'entries': [
    dict(variant='pm-client', role='EndDevice', build='1.2.5-bseedcli26', imageType=65024,
         manufacturerCode=4417, fileVersion=0x12053025, otaSha256='c' * 64),
    dict(variant='pm-router', role='Router', build='1.2.5-bseedr23', imageType=43556,
         manufacturerCode=4417, fileVersion=0x12053025, otaSha256='r' * 64),
    dict(variant='nonpm-router', role='Router', build='1.1.3-bseedr20', imageType=43555,
         manufacturerCode=4417, fileVersion=0x1102301F, otaSha256='n' * 64),
]}


def live(manufacturer, model, role, build):
    return {'friendly_name': 'SocketX', 'ieee_address': IEEE, 'manufacturer': manufacturer,
            'model_id': model, 'type': role, 'software_build_id': build, 'network_address': 1234,
            'interview_state': 'SUCCESSFUL', 'relay_state':'ON'}


STOCK = live('_TZ3000_b28wrpvx', 'TS011F', 'Router', '1.0.13')
PM_CLIENT = live('b28wrpvx', 'TS011F-BS-PM', 'EndDevice', '1.2.5-bseedcli25')
PM_ROUTER = live('b28wrpvx', 'TS011F-BS-PM', 'Router', '1.2.5-bseedr22')
NONPM_ROUTER = live('o1jzcxou', 'TS011F-BS', 'Router', '1.1.3-bseedr19')


def site(tmp_path):
    return {'publish': {'url_base': 'http://ha.invalid/local/bseed'}, 'serve_root': str(tmp_path / 'serve'),
            'mqtt_config': str(tmp_path / 'mqtt.yaml'), 'broker': 'broker.invalid',
            'network_lock_dir': str(tmp_path / 'locks'), 'network_id': '0x00124b0000000000',
            'pm_ssh_host': 'ha.invalid', 'pm_ssh_key': str(tmp_path / 'key')}


def planned(tmp_path, row, to):
    route = mig.choose_route(row, to)
    candidate = mig.candidate_for(PACKAGE, route)
    wrapped = route['wrapper_type'] is not None
    profile = mig.build_profile(site(tmp_path), row, route, candidate, image=tmp_path / 'serve' / 'x.ota',
                                sha256='a' * 64, native_image=tmp_path / 'native.ota' if wrapped else None,
                                native_sha256=candidate['otaSha256'] if wrapped else None,
                                join_via='RouterY' if route['source'] != route['target'] else None,
                                workdir=tmp_path / 'work', folder='SocketX-plan',
                                preflash_file_version=0x12053024)
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    return route, profile, campaign.load_profile(path)


@pytest.mark.parametrize('row, to, kind', [
    (PM_CLIENT, 'same', 'pm_same_role'),
    (PM_CLIENT, 'router', 'pm_role_change'),
    (PM_ROUTER, 'client', 'pm_role_change'),
    (NONPM_ROUTER, 'same', 'nonpm_same_role'),
])
def test_every_route_builds_a_profile_the_campaign_accepts(tmp_path, row, to, kind):
    route, profile, loaded = planned(tmp_path, row, to)
    assert route['kind'] == kind
    assert relay.validate(loaded) is False and loaded['expect_relay'] == 'ON'
    assert loaded['auto_relay_off'] is False and loaded['relay_state_policy'] == 'preserve'
    assert loaded['operator_connected_load_risk_accepted'] is True
    assert loaded['block_bytes'] == 48 and loaded['response_delay_ms'] >= 1200
    if route['source'] != route['target']:
        assert campaign.join_mode(loaded) == 'network-wide'
        assert '--network-wide' in campaign.rejoin_cmd(loaded, loaded['ieee'], tmp_path / 'e.json')
    assert loaded['preserve_device_state'] is True


def test_custom_socket_already_off_remains_off_with_any_load(tmp_path):
    device=dict(PM_CLIENT,relay_state='OFF')
    _,profile,_=planned(tmp_path,device,'same')
    assert profile['expect_relay']=='OFF'
    assert profile['auto_relay_off'] is False
    assert 'max_reported_watts' not in profile


def test_no_verified_relay_baseline_refuses_to_guess(tmp_path):
    source=dict(PM_CLIENT)
    source.pop('relay_state')
    with pytest.raises(mig.Refused,match="baseline"):
        planned(tmp_path,source,'same')


def test_route_specifics(tmp_path):
    # A non-pinned stock-to-Client route MUST refuse instead of turning OFF.
    with pytest.raises(mig.Refused,match="pinned owner-authorized"):
        planned(tmp_path,STOCK,'client')
    _, same, _ = planned(tmp_path, PM_CLIENT, 'same')
    assert (same['image_type'], same['preflash_build'], same['auto_relay_off']) == (65024, '1.2.5-bseedcli25', False)
    _, force, _ = planned(tmp_path, PM_CLIENT, 'router')
    assert force['force_test_transition'] is True and force['image_type'] == 65024
    assert force['preflash_query_image_type'] == 65024 and force['preflash_file_version'] == '0x12053024'
    _, nonpm, _ = planned(tmp_path, NONPM_ROUTER, 'same')
    assert nonpm['non_pm'] is True and nonpm['require_pm'] is False
    assert nonpm['preflash_relay_physical_mode'] == 'follow_state' and nonpm['auto_relay_off'] is False


@pytest.mark.parametrize('row, to, match', [
    (STOCK, 'router', 'stock-to-Router'),
    (live('o1jzcxou', 'TS011F-BS', 'Router', 'x'), 'client', 'Non-PM role changes'),
    (live('_TZ3000_o1jzcxou', 'TS011F', 'Router', None), 'client', 'Unsupported board'),
    (live('b28wrpvx', 'TS011F-BS-PM', 'Coordinator', 'x'), 'same', 'not a socket role'),
])
def test_unsupported_routes_are_refused_with_a_reason(row, to, match):
    with pytest.raises(mig.Refused, match=match):
        mig.choose_route(row, to)


def test_mixed_targets_have_explicit_roles_and_reject_duplicates():
    assert mig.parse_targets(['A=router', 'B=client', 'C=same']) == [
        ('A', 'router'), ('B', 'client'), ('C', 'same'),
    ]
    with pytest.raises(mig.Refused, match='Duplicate target'):
        mig.parse_targets(['A=client', 'a=router'])
    with pytest.raises(mig.Refused, match='DEVICE='):
        mig.parse_targets(['../A=client'])


def test_mixed_queue_prepares_all_then_runs_parallel_same_role_and_exclusive_transition(tmp_path, monkeypatch):
    args = SimpleNamespace(target=['A=router', 'B=client', 'C=client'], device=None, to=None,
                           max_resumes=3, lease_wait_seconds=0, release_authority=None)
    roles = [('Router', 'Router'), ('EndDevice', 'EndDevice'), ('Router', 'EndDevice')]
    profiles = {}
    for index, (target, roles_for_target) in enumerate(zip('ABC', roles), start=1):
        work = tmp_path / target
        work.mkdir()
        profiles[target] = (work, {'device': target, 'ieee': f'0x{index:016x}',
                                  'preflash_role': roles_for_target[0], 'postflash_role': roles_for_target[1],
                                  'image': f'{target}.ota', 'sha256': target * 64,
                                  'parallel_ota_max': 2, 'source_rejoin_strategy': 'none'})
    profiles['A'][1]['concurrent_ota_ieees'] = [profiles['B'][1]['ieee']]
    profiles['B'][1]['concurrent_ota_ieees'] = [profiles['A'][1]['ieee']]
    prepared = []
    order = []

    def prepare(_site, target):
        prepared.append(target.device)
        return (target, *profiles[target.device], False)

    def publish(_site, target, *, work):
        assert set(prepared) == set('ABC')
        order.append(('publish', target.device))

    def mark_candidate(device):
        work, profile = profiles[device]
        lock = {key: profile[key] for key in ('device', 'ieee', 'sha256')}
        (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(lock, phase='postflash_candidate')))

    def parallel(paths, confirmations, *, extra):
        assert [p.parent.name for p in paths] == ['A', 'B']
        assert list(confirmations) == [profiles[device][1]['ieee'] for device in 'AB']
        order.append(('parallel', 'AB'))
        for device in 'AB':
            mark_candidate(device)
        return [{'device': device, 'state': 'installed'} for device in 'AB']

    def transition(_site, target, *, work):
        order.append(('exclusive', target.device))
        mark_candidate(target.device)
        return 0

    monkeypatch.setattr(mig, 'planned_target', prepare)
    monkeypatch.setattr(mig, 'cmd_publish', publish)
    monkeypatch.setattr(mig, 'active_leases', lambda _site: [])
    monkeypatch.setattr(mig, 'check_parallel_topology', lambda _site, _wave: None)
    monkeypatch.setattr(mig, 'cmd_run', transition)
    monkeypatch.setattr('bseed_ota_autopilot.run_many', parallel)
    assert mig.cmd_queue({}, args) == 0
    assert set(order[:3]) == {('publish', target) for target in 'ABC'}
    assert order[3:] == [('parallel', 'AB'), ('exclusive', 'C')]


def test_mixed_queue_does_not_publish_if_one_role_plan_is_refused(monkeypatch):
    args = SimpleNamespace(target=['A=router', 'B=client'], device=None, to=None, max_resumes=3)
    def prepare(_site, target):
        if target.device == 'B':
            raise mig.Refused('The saved plan has the wrong role')
        return target
    monkeypatch.setattr(mig, 'planned_target', prepare)
    monkeypatch.setattr(mig, 'cmd_publish', lambda *args, **kwargs: pytest.fail('Published a partial batch'))
    with pytest.raises(mig.Refused, match='wrong role'):
        mig.cmd_queue({}, args)


def test_parallel_wave_detects_router_child_conflict(monkeypatch):
    router = dict(PM_ROUTER, friendly_name='RouterA')
    child = dict(PM_CLIENT, friendly_name='ClientB', ieee_address='0x0000000000000002')
    profiles = [(None, None, {'device': row['friendly_name'], 'ieee': row['ieee_address'],
                              'preflash_role': row['type']}, False) for row in (router, child)]
    monkeypatch.setattr(mig, 'inventory', lambda _site: [router, child])
    monkeypatch.setattr('bseed_zdo_live.read_neighbor_table', lambda *args, **kwargs: [
        {'eui64': child['ieee_address'], 'relationship': 1},
    ])
    assert mig.check_parallel_topology({'mqtt_config': 'config', 'broker': 'broker'}, profiles) == (
        'RouterA lists another member as its child'
    )


def test_wrapper_changes_only_the_header_type_and_version():
    native = bytes(range(256)) * 4
    out = mig.wrap(native, 54179)
    assert struct.unpack_from('<H', out, 12)[0] == 54179 and struct.unpack_from('<I', out, 14)[0] == 0xFFFFFFFF
    assert [i for i in range(len(native)) if native[i] != out[i]] == list(range(12, 18))


def test_join_via_is_strongest_router_and_children_are_reported():
    devices = [dict(ieee_address='0x1', friendly_name='Stock', type='Router', software_build_id=None),
               dict(ieee_address='0x2', friendly_name='Custom', type='Router', software_build_id='1.2.5-bseedr22'),
               dict(ieee_address='0x3', friendly_name='Dimmer', type='EndDevice'),
               dict(ieee_address=IEEE, friendly_name='SocketX', type='Router')]
    neighbors = [dict(eui64='0x1', lqi=150, relationship=2), dict(eui64='0x2', lqi=150, relationship=0),
                 dict(eui64='0x3', lqi=0, relationship=1), dict(eui64=IEEE, lqi=255, relationship=2)]
    assert mig.choose_join_via(neighbors, devices, IEEE) == ('Custom', 150, ['Dimmer'])
    neighbors[0]['lqi'] = 200
    assert mig.choose_join_via(neighbors, devices, IEEE)[0] == 'Stock'
    # No Router neighbour: the network-wide rejoin needs none, and the
    # children that will have to re-parent are still reported.
    assert mig.choose_join_via([dict(eui64='0x3', lqi=9, relationship=1)], devices, IEEE)[:2] == (None, None)


def test_resume_only_while_progress_increases_and_budget_remains():
    assert mig.resume_allowed([{'max_progress': 38.8}], 0, 3)[0] is True
    assert mig.resume_allowed([{'max_progress': 38.8}, {'max_progress': 38.8}], 1, 3)[0] is False
    assert mig.resume_allowed([{'max_progress': 38.8}, {'max_progress': 60}], 3, 3)[0] is False
    assert mig.resume_allowed([], 0, 3)[0] is False


def test_release_needs_candidate_matching_token_restored_relay_and_words(tmp_path):
    profile = {'ieee': IEEE, 'device': 'SocketX', 'postflash_build': 'b', 'workdir': str(tmp_path)}
    lease = {'ieee': IEEE, 'phase': 'postflash_candidate', 'token': 't1', '_path': tmp_path / 'lease.json'}
    lock = {'ieee': IEEE, 'phase': 'postflash_candidate', 'token': 't1'}
    request = mig.release_request(lease, lock, profile, 'Owner: "SocketX is good"')
    assert request['operation'] == 'quarantine' and request['limits']['max_files'] == 1
    for bad in (dict(lease, phase='ota_running'), dict(lease, token='t2'), dict(lease, ieee='0x0')):
        with pytest.raises(mig.Refused):
            mig.release_request(bad, lock, profile, 'Owner accepted it')
    with pytest.raises(mig.Refused, match='acceptance words'):
        mig.release_request(lease, lock, profile, '')
    (tmp_path / 'RELAY_AUTO_OFF.json').write_text(json.dumps({'restored': False}))
    with pytest.raises(mig.Refused, match='not restored'):
        mig.release_request(lease, lock, profile, 'Owner accepted it')


def test_publish_uploads_the_image_before_prepare_checks_it(tmp_path, monkeypatch):
    # prepare fetches the served image over HTTP, so it must run after the
    # image upload and before the index (which prepare writes) is uploaded.
    work = tmp_path / 'work'
    work.mkdir()
    profile = {'image': str(tmp_path / 'SocketX-a-to-b.ota'), 'index_output': str(tmp_path / 'index.json'),
               'url': 'http://ha.invalid/local/bseed/SocketX-a-to-b/firmware.ota?v=1',
               'sha256': mig.hashlib.sha256(b'image').hexdigest()}
    (work / 'profile.json').write_text(json.dumps(profile))
    calls = []
    monkeypatch.setattr(mig, 'workdir_for', lambda _site, _device: work)
    monkeypatch.setattr(mig.subprocess, 'call', lambda cmd: calls.append(cmd[0] + ':' + cmd[-1].rsplit('/', 1)[-1]) or 0)
    monkeypatch.setattr(mig, 'campaign', lambda _path, mode: calls.append('campaign:' + mode) or 0)

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            calls.append('verify')
            return b'image'

    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, timeout: Reply())
    site = {'publish': {'remote_dir': '/config/www/bseed', 'ssh_alias': 'ha'}}
    assert mig.cmd_publish(site, type('A', (), {'device': 'SocketX'})()) == 0
    assert calls == ['ssh:SocketX-a-to-b', 'scp:firmware.ota',
                     'campaign:prepare', 'scp:index.json', 'verify']

    # A failed prepare never uploads an index.
    calls.clear()
    monkeypatch.setattr(mig, 'campaign', lambda _path, mode: calls.append('campaign:' + mode) or 1)
    with pytest.raises(SystemExit, match='prepare failed'):
        mig.cmd_publish(site, type('A', (), {'device': 'SocketX'})())
    assert 'scp:index.json' not in calls


def test_neighbor_page_parsing_is_strict():
    total, entries = parse_neighbor_page({'status': 'ok', 'data': [0, {'neighborTableEntries': 2,
                                                                         'entryList': [{}, {}]}]})
    assert (total, len(entries)) == (2, 2)
    for bad in ({'status': 'error'}, {'status': 'ok', 'data': [1, {}]},
                {'status': 'ok', 'data': [0, {'entryList': []}]}):
        with pytest.raises(ValueError):
            parse_neighbor_page(bad)
