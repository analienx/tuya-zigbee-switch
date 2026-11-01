"""One-command canary: derived profile, sealed-byte refusal and gated stage order."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import bseed_canary as canary  # noqa: E402
from bseed_image_fixture import image_for  # noqa: E402
from bseed_ota_campaign import load_profile  # noqa: E402

IEEE = '0xa4c1380000000001'


def make_artifacts(tmp_path, board):
    artifacts = tmp_path / 'artifacts'
    entries, registry = [], {}
    for role in ('Router', 'EndDevice'):
        cand = canary.candidate(board, role)
        data = image_for(cand)
        variant = '%s-%s' % (board, canary.ROLE_KEYS[role])
        (artifacts / variant).mkdir(parents=True)
        (artifacts / variant / 'firmware.ota').write_bytes(data)
        entries.append({'variant': variant, 'build': cand['build'], 'imageType': cand['type'],
                        'fileVersion': cand['version'], 'sourceCommit': 'f' * 40,
                        'otaSha256': hashlib.sha256(data).hexdigest(),
                        'otaSha512': hashlib.sha512(data).hexdigest()})
        registry[cand['type']] = {'versions': [{
            'file_version': cand['version'], 'version_str': cand['build'],
            'sha512': hashlib.sha512(data).hexdigest()}]}
    (artifacts / 'CANDIDATE_PACKAGE.json').write_text(json.dumps({'entries': entries}))
    return artifacts, registry


def make_site(tmp_path, board, role, **device_extra):
    private = tmp_path / 'private'
    device = {'ieee': IEEE, 'board': board, 'role': role,
              'preflash_build': 'previous', 'expect_relay': 'OFF'}
    if board == 'nonpm':
        device['relay_physical_mode'] = 'attached'
    if board == 'pm' and role == 'Router':
        baseline = private / 'baseline.json'
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text('{}')
        device['pm_settings_baseline'] = str(baseline)
    device.update(device_extra)
    site = {'mqtt_config': str(private / 'configuration.yaml'), 'broker': '192.0.2.10',
            'serve_root': str(private / 'serve'), 'serve_host': '192.0.2.20',
            'serve_bind': '127.0.0.1', 'serve_port': 0,
            'campaign_root': str(private / 'campaigns'),
            'network_lock_dir': str(private / 'locks'), 'network_id': '0x00124b0000000000',
            'pm_ssh_host': 'z2m-host', 'pm_ssh_key': str(private / 'key'),
            'devices': {'Canary': device}}
    path = tmp_path / 'site.json'
    path.write_text(json.dumps(site))
    return canary.load_site(path)


@pytest.mark.parametrize('board,role', [('pm', 'EndDevice'), ('pm', 'Router'),
                                        ('nonpm', 'EndDevice'), ('nonpm', 'Router')])
def test_profile_derived_from_sealed_candidate_passes_campaign_validation(tmp_path, board, role):
    artifacts, registry = make_artifacts(tmp_path, board)
    site = make_site(tmp_path, board, role)
    path, profile = canary.build_profile(site, 'Canary', artifacts, registry)
    cand = canary.candidate(board, role)
    assert profile['postflash_build'] == cand['build']
    assert profile['preflash_role'] == profile['postflash_role'] == role
    assert int(profile['file_version'], 16) == cand['version']
    assert Path(profile['image']).read_bytes() == (artifacts / ('%s-%s' % (
        board, canary.ROLE_KEYS[role])) / 'firmware.ota').read_bytes()
    loaded = load_profile(path)
    assert loaded['require_pm'] is (board == 'pm') and loaded['non_pm'] is (board == 'nonpm')
    if board == 'pm' and role == 'Router':
        matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
        assert {a['role'] for a in matrix['artifacts']} == {'Router', 'EndDevice'}
        assert matrix['hardwareAcceptance'] is False
    # Re-planning the same candidate is idempotent.
    assert canary.build_profile(site, 'Canary', artifacts, registry)[0] == path


def test_unsealed_or_altered_bytes_are_refused(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = make_site(tmp_path, 'pm', 'EndDevice')
    cand = canary.candidate('pm', 'EndDevice')
    with pytest.raises(canary.CanaryError, match='not sealed'):
        canary.build_profile(site, 'Canary', artifacts, {cand['type']: {'versions': []}})
    ota = artifacts / 'pm-client' / 'firmware.ota'
    data = bytearray(ota.read_bytes())
    data[-1] ^= 1
    ota.write_bytes(bytes(data))
    with pytest.raises(canary.CanaryError, match='differ from the sealed'):
        canary.build_profile(site, 'Canary', artifacts, registry)


def write_lock(root, phase):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'ACTIVE_LOCK.json').write_text(json.dumps({'phase': phase, 'ieee': IEEE}))


def test_unresolved_prior_lock_blocks_new_build_in_another_workdir(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = make_site(tmp_path, 'pm', 'Router', prior_workdirs=[str(tmp_path / 'old')])
    write_lock(tmp_path / 'old' / 'campaign', 'update_error')
    with pytest.raises(canary.CanaryError, match='Unresolved campaign lock'):
        canary.build_profile(site, 'Canary', artifacts, registry)


def test_settled_prior_lock_does_not_block(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = make_site(tmp_path, 'pm', 'EndDevice', prior_workdirs=[str(tmp_path / 'old')])
    write_lock(tmp_path / 'old' / 'campaign', 'postflash_candidate')
    canary.build_profile(site, 'Canary', artifacts, registry)


def test_request_timeout_defaults_to_five_minutes_and_device_may_override(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    (tmp_path / 'default').mkdir()
    (tmp_path / 'long').mkdir()
    site = make_site(tmp_path / 'default', 'pm', 'EndDevice')
    path, profile = canary.build_profile(site, 'Canary', artifacts, registry)
    assert profile['request_timeout_ms'] == 300000
    assert load_profile(path)['request_timeout_ms'] == 300000
    site = make_site(tmp_path / 'long', 'pm', 'EndDevice',
                     overrides={'request_timeout_ms': 1800000})
    _, profile = canary.build_profile(site, 'Canary', artifacts, registry)
    assert profile['request_timeout_ms'] == 1800000


def test_site_and_identity_overrides_are_guarded(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = make_site(tmp_path, 'pm', 'EndDevice', overrides={'sha256': '0' * 64})
    with pytest.raises(canary.CanaryError, match='identity fields'):
        canary.build_profile(site, 'Canary', artifacts, registry)
    inside = ROOT / 'site.json'
    with pytest.raises(canary.CanaryError, match='outside the repository'):
        canary.load_site(inside)
    site = make_site(tmp_path, 'pm', 'Router', pm_settings_baseline=None)
    with pytest.raises(canary.CanaryError, match='pm_settings_baseline'):
        canary.build_profile(site, 'Canary', artifacts, registry)


class Recorder:
    def __init__(self, fail_mode=None):
        self.modes, self.calls, self.fail_mode = [], [], fail_mode

    def __call__(self, command):
        mode = command[command.index('--mode') + 1]
        self.modes.append(mode)
        self.calls.append(command)
        return 1 if mode == self.fail_mode else 0


def planned(tmp_path, board, role='EndDevice'):
    artifacts, registry = make_artifacts(tmp_path, board)
    site = make_site(tmp_path, board, role)
    path, profile = canary.build_profile(site, 'Canary', artifacts, registry)
    return site, path, profile


def test_default_run_is_read_only_and_board_specific(tmp_path):
    site, path, profile = planned(tmp_path / 'pm', 'pm')
    calls = Recorder()
    assert canary.run(site, path, profile, call=calls) == 0
    assert calls.modes == ['prepare', 'preflight', 'check']
    site, path, profile = planned(tmp_path / 'nonpm', 'nonpm')
    calls = Recorder()
    assert canary.run(site, path, profile, call=calls) == 0
    assert calls.modes == ['prepare', 'preflight', 'qualify']
    assert all('--confirm-ieee' not in c for c in calls.calls)


def test_flash_needs_exact_confirmation_before_any_stage(tmp_path):
    site, path, profile = planned(tmp_path / 'pm', 'pm')
    calls = Recorder()
    with pytest.raises(canary.CanaryError, match='exactly'):
        canary.run(site, path, profile, flash=True, confirm_ieee='0xa4c1380000000002', call=calls)
    with pytest.raises(canary.CanaryError, match='only accepted together'):
        canary.run(site, path, profile, confirm_ieee=IEEE, call=calls)
    assert calls.modes == []
    site, path, profile = planned(tmp_path / 'nonpm', 'nonpm')
    with pytest.raises(canary.CanaryError, match='confirm-load-unplugged'):
        canary.run(site, path, profile, flash=True, confirm_ieee=IEEE, call=calls)
    assert calls.modes == []


def test_flash_runs_after_gates_and_stops_on_first_failure(tmp_path):
    site, path, profile = planned(tmp_path / 'nonpm', 'nonpm')
    calls = Recorder()
    assert canary.run(site, path, profile, flash=True, confirm_ieee=IEEE,
                      confirm_unloaded=True, call=calls) == 0
    assert calls.modes == ['prepare', 'preflight', 'qualify', 'flash', 'status']
    flash = calls.calls[3]
    assert flash[flash.index('--confirm-ieee') + 1] == IEEE and '--confirm-load-unplugged' in flash
    calls = Recorder(fail_mode='qualify')
    assert canary.run(site, path, profile, flash=True, confirm_ieee=IEEE,
                      confirm_unloaded=True, call=calls) == 1
    assert calls.modes == ['prepare', 'preflight', 'qualify']


def test_lan_server_serves_only_the_private_root(tmp_path):
    root = tmp_path / 'serve'
    root.mkdir()
    (root / 'image.ota').write_bytes(b'payload')
    with canary.LanServer(root, '127.0.0.1', 0) as server:
        port = server.httpd.server_address[1]
        with urllib.request.urlopen('http://127.0.0.1:%d/image.ota' % port, timeout=5) as rsp:
            assert rsp.read() == b'payload'


def live_record(board='pm', role='EndDevice', build='1.2.5-bseedcli12', **extra):
    spec = canary.BOARDS[board]
    record = {'ieeeAddr': IEEE, 'type': role, 'swBuildId': build,
              'manufName': spec['manufacturer'], 'modelId': spec['model'],
              'interviewCompleted': True}
    record.update(extra)
    return record


def test_discovery_fills_build_and_refuses_disagreement(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = make_site(tmp_path, 'pm', 'EndDevice', preflash_build=None)
    with pytest.raises(canary.CanaryError, match='needs preflash_build'):
        canary.build_profile(site, 'Canary', artifacts, registry)
    _, profile = canary.build_profile(site, 'Canary', artifacts, registry, live=live_record())
    assert profile['preflash_build'] == '1.2.5-bseedcli12'
    for bad, match in ((live_record(role='Router'), 'use a transition'),
                       (live_record(board='nonpm'), 'live board'),
                       (live_record(interviewCompleted=False), 'interview')):
        with pytest.raises(canary.CanaryError, match=match):
            canary.discover(site, 'Canary', live=bad)
    (tmp_path / 'b').mkdir()
    site = make_site(tmp_path / 'b', 'pm', 'EndDevice', preflash_build='1.2.5-bseedcli9')
    with pytest.raises(canary.CanaryError, match='differs from live'):
        canary.discover(site, 'Canary', live=live_record())


def test_discovery_finds_locks_by_ieee_and_accepts_terminal_phases(tmp_path):
    scratch = tmp_path / 'scratch'
    site = make_site(tmp_path, 'pm', 'EndDevice', preflash_build=None)
    site['lock_search_roots'] = [str(scratch)]
    other = scratch / 'other' / 'run'
    other.mkdir(parents=True)
    (other / 'ACTIVE_LOCK.json').write_text(json.dumps(
        {'phase': 'update_error', 'ieee': '0xa4c1380000000099', 'device': 'Other'}))
    for index, phase in enumerate(('postflash_accepted', 'source_unchanged_abandoned',
                                   'installed_image_reconciled')):
        write_lock(scratch / ('done%d' % index), phase)
    report = canary.discover(site, 'Canary', live=live_record())
    assert len(report['locks']) == 3 and all(lock['clean'] for lock in report['locks'])
    assert report['eligible'] and report['preflash_build'] == '1.2.5-bseedcli12'
    write_lock(scratch / 'stuck', 'update_timeout_or_unconfirmed')
    report = canary.discover(site, 'Canary', live=live_record())
    assert not report['eligible']
    with pytest.raises(canary.CanaryError, match='update_timeout_or_unconfirmed'):
        canary.prior_campaign_locks(site, 'Canary')


def test_live_identity_uses_read_only_ssh_query(tmp_path):
    site = make_site(tmp_path, 'pm', 'EndDevice')
    site['ha_ssh_alias'] = 'ha'
    seen = {}

    def fake_run(command, **kwargs):
        seen['command'], seen['input'] = command, kwargs['input']
        return type('R', (), {'returncode': 0,
                              'stdout': json.dumps(live_record()) + '\n'})()

    assert canary.live_identity(site, IEEE, run=fake_run)['swBuildId'] == '1.2.5-bseedcli12'
    assert seen['command'][:5] == ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
    assert seen['command'][-2:] == ['/config/zigbee2mqtt/database.db', IEEE]
    assert 'write' not in seen['input'] and 'open(sys.argv[1]' in seen['input']

    def no_record(command, **kwargs):
        return type('R', (), {'returncode': 0, 'stdout': ''})()

    with pytest.raises(canary.CanaryError, match='unavailable'):
        canary.live_identity(site, IEEE, run=no_record)


def publish_site(tmp_path, board='pm', role='EndDevice'):
    site = make_site(tmp_path, board, role)
    site['publish'] = {'ssh_alias': 'ha', 'remote_dir': '/config/www/bseed-canary',
                       'url_base': 'http://192.0.2.58:8123/local/bseed-canary'}
    return site


class PublishRecorder:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()


def test_ha_publish_urls_upload_after_prepare_and_always_clean_up(tmp_path):
    artifacts, registry = make_artifacts(tmp_path, 'pm')
    site = publish_site(tmp_path)
    path, profile = canary.build_profile(site, 'Canary', artifacts, registry)
    folder = 'http://192.0.2.58:8123/local/bseed-canary/Canary-' + profile['postflash_build']
    assert profile['url'].startswith(folder + '/firmware.ota?v=')
    assert profile['index_url'].startswith(folder + '/index.json?v=')
    assert load_profile(path)['url'] == profile['url']
    published = PublishRecorder()
    calls = Recorder()
    assert canary.run(site, path, profile, call=calls, publish_run=published) == 0
    assert calls.modes == ['prepare', 'preflight', 'check']
    flat = [' '.join(c) for c in published.commands]
    remote = '/config/www/bseed-canary/Canary-' + profile['postflash_build']
    assert "mkdir -p '%s'" % remote in flat[0]
    assert flat[1].startswith('scp') and flat[1].endswith(remote + '/firmware.ota')
    assert flat[2].startswith('scp') and flat[2].endswith(remote + '/index.json')
    assert "rm -rf -- '%s'" % remote in flat[-1]
    # The image is served before prepare verifies it; the index only after.
    assert published.commands.index(published.commands[1]) < 2
    # A failing stage still unpublishes, including a failed prepare.
    for stage in ('check', 'prepare'):
        published, calls = PublishRecorder(), Recorder(fail_mode=stage)
        assert canary.run(site, path, profile, call=calls, publish_run=published) == 1
        assert "rm -rf" in ' '.join(published.commands[-1])
    assert not any(c[-1].endswith('index.json') for c in published.commands)


def test_publish_site_validation(tmp_path):
    site = make_site(tmp_path, 'pm', 'EndDevice')
    raw = json.loads((tmp_path / 'site.json').read_text())
    raw.pop('serve_host'); raw.pop('serve_port')
    (tmp_path / 'site.json').write_text(json.dumps(raw))
    with pytest.raises(canary.CanaryError, match='publish or serve_host'):
        canary.load_site(tmp_path / 'site.json')
    raw['publish'] = {'ssh_alias': 'ha', 'remote_dir': 'relative', 'url_base': 'http://x'}
    (tmp_path / 'site.json').write_text(json.dumps(raw))
    with pytest.raises(canary.CanaryError, match='absolute remote path'):
        canary.load_site(tmp_path / 'site.json')
    assert site
