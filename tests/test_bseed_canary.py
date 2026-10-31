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
