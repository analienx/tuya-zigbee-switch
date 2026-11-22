"""One-command socket migration planning. GitHub-hosted CI only; no network."""
import json
from pathlib import Path
import struct
import sys

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
            'interview_state': 'SUCCESSFUL'}


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
    (STOCK, 'client', 'stock_pm_to_client'),
    (PM_CLIENT, 'same', 'pm_same_role'),
    (PM_CLIENT, 'router', 'pm_role_change'),
    (PM_ROUTER, 'client', 'pm_role_change'),
    (NONPM_ROUTER, 'same', 'nonpm_same_role'),
])
def test_every_route_builds_a_profile_the_campaign_accepts(tmp_path, row, to, kind):
    route, profile, loaded = planned(tmp_path, row, to)
    assert route['kind'] == kind
    assert relay.validate(loaded) is True and loaded['expect_relay'] == 'OFF'
    assert loaded['block_bytes'] == 48 and loaded['response_delay_ms'] >= 1200
    if route['source'] != route['target']:
        assert campaign.join_mode(loaded) == 'network-wide'
        assert '--network-wide' in campaign.rejoin_cmd(loaded, loaded['ieee'], tmp_path / 'e.json')
    assert loaded['preserve_device_state'] is True


def test_route_specifics(tmp_path):
    _, stock, _ = planned(tmp_path, STOCK, 'client')
    assert (stock['image_type'], stock['file_version'], stock['relay_get_key']) == (54179, '0xffffffff', 'state')
    assert stock['stock_client_conversion'] is True and 'preflash_build' not in stock
    assert relay.proof(stock) == 'relay_off'
    _, same, _ = planned(tmp_path, PM_CLIENT, 'same')
    assert (same['image_type'], same['preflash_build'], relay.proof(same)) == (65024, '1.2.5-bseedcli25', 'meter')
    _, force, _ = planned(tmp_path, PM_CLIENT, 'router')
    assert force['force_test_transition'] is True and force['image_type'] == 65024
    assert force['preflash_query_image_type'] == 65024 and force['preflash_file_version'] == '0x12053024'
    _, nonpm, _ = planned(tmp_path, NONPM_ROUTER, 'same')
    assert nonpm['non_pm'] is True and nonpm['require_pm'] is False
    assert nonpm['preflash_relay_physical_mode'] == 'follow_state' and relay.proof(nonpm) == 'relay_off'


@pytest.mark.parametrize('row, to, match', [
    (STOCK, 'same', 'explicit'),
    (live('o1jzcxou', 'TS011F-BS', 'Router', 'x'), 'client', 'Non-PM role changes'),
    (live('_TZ3000_o1jzcxou', 'TS011F', 'Router', None), 'client', 'Unsupported board'),
    (live('b28wrpvx', 'TS011F-BS-PM', 'Coordinator', 'x'), 'same', 'not a socket role'),
])
def test_unsupported_routes_are_refused_with_a_reason(row, to, match):
    with pytest.raises(mig.Refused, match=match):
        mig.choose_route(row, to)


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
