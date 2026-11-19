"""Campaign orchestration of group/reporting preservation; no live MQTT."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_campaign as campaign


@pytest.fixture(autouse=True)
def no_telemetry_release(monkeypatch):
    monkeypatch.setattr('bseed_pm_telemetry_guard.release', lambda *args: None)


def base_profile(tmp_path, **changes):
    image = tmp_path / 'image.ota'
    image.write_bytes(b'unsigned offline fixture')
    template = tmp_path / 'template.json'
    template.write_text('[]')
    cfg = {'device': 'TestSocket', 'ieee': '0x0011223344556677',
           'manufacturer': '_TZ3000_b28wrpvx', 'model': 'TS011F', 'preflash_role': 'Router',
           'image': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
           'url': 'http://example.invalid/image.ota', 'mqtt_config': str(tmp_path / 'mqtt.yaml'),
           'broker': '127.0.0.1', 'workdir': str(tmp_path / 'campaign'), 'manufacturer_code': 4417,
           'image_type': 54179, 'file_version': '0xffffffff', 'expect_relay': 'ON',
           'index_url': 'http://example.invalid/index.json',
           'index_output': str(tmp_path / 'index.json'), 'template_index': str(template),
           'postflash_role': 'EndDevice', 'postflash_build': 'expected-client',
           'join_via': 'KnownRouter', 'join_mode': 'network-wide',
           'preserve_device_state': True}
    cfg.update(changes)
    if cfg['preflash_role'] == cfg['postflash_role']:
        cfg.pop('join_mode', None)  # same-role updates have no join window
    return cfg


def write(tmp_path, cfg, name='profile.json'):
    path = tmp_path / name
    path.write_text(json.dumps(cfg))
    return path


def fake_calls(results=None):
    """Mock subprocesses; the snapshot step writes its pre-OTA evidence."""
    results = dict(results or {})
    calls = []

    def call(cmd):
        name = Path(cmd[2]).name
        step = name + (':' + cmd[3] if name == 'bseed_z2m_device_state.py' else '')
        calls.append((step, cmd))
        if step == 'bseed_z2m_device_state.py:snapshot' and results.get(step, 0) == 0:
            Path(cmd[cmd.index('--output') + 1]).write_text('{"kind": "device_state_snapshot"}')
        return results.get(step, 0)
    return call, calls


def run(monkeypatch, path, *argv, results=None):
    call, calls = fake_calls(results)
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), *argv])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=call), \
         patch('bseed_ota_campaign.record_postflash_candidate', return_value=True):
        with pytest.raises(SystemExit) as done:
            campaign.main()
    return done.value.code, calls


def test_transition_snapshots_before_flash_and_restores_before_postflash(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 0
    assert [step for step, _ in calls] == [
        'bseed_z2m_device_state.py:snapshot', 'bseed_targeted_z2m_ota.py',
        'bseed_z2m_rejoin_window.py', 'bseed_z2m_metadata_refresh.py',
        'bseed_z2m_device_state.py:restore', 'bseed_z2m_postflash_verify.py']
    snapshot, restore = calls[0][1], calls[4][1]
    assert snapshot[snapshot.index('--relay-endpoint') + 1] == '1'   # stock layout
    assert restore[restore.index('--relay-endpoint') + 1] == '2'     # custom layout
    assert restore[restore.index('--confirm-ieee') + 1] == cfg['ieee']
    assert '--configure' not in restore  # Router -> Client: PM provisioning owns reporting
    join = calls[2][1]
    from bseed_z2m_rejoin_window import MAX_NETWORK_WIDE_SECONDS
    assert '--network-wide' in join and '--permit-via' not in join  # owner rule: join from all
    assert join[join.index('--total-seconds') + 1] == str(
        min(campaign.DEFAULT_JOIN_TOTAL_SECONDS, MAX_NETWORK_WIDE_SECONDS))


def test_client_to_router_transition_reconfigures_by_default(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path, manufacturer='b28wrpvx', model='TS011F-BS-PM',
                       preflash_role='EndDevice', postflash_role='Router',
                       postflash_build='expected-router')
    monkeypatch.setattr(campaign, 'validate_pm_mode', lambda data: None)
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 0
    snapshot = dict(calls)['bseed_z2m_device_state.py:snapshot']
    restore = dict(calls)['bseed_z2m_device_state.py:restore']
    assert snapshot[snapshot.index('--relay-endpoint') + 1] == '2'
    assert '--configure' in restore


def test_failed_snapshot_blocks_the_firmware_write(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'],
                      results={'bseed_z2m_device_state.py:snapshot': 2})
    assert code == 2 and [step for step, _ in calls] == ['bseed_z2m_device_state.py:snapshot']


def test_resumed_campaign_keeps_its_original_snapshot(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    work = Path(cfg['workdir'])
    work.mkdir(parents=True)
    original = work / campaign.STATE_SNAPSHOT
    original.write_text('{"kind": "device_state_snapshot", "original": true}')
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 0 and 'bseed_z2m_device_state.py:snapshot' not in dict(calls)
    assert json.loads(original.read_text())['original'] is True


def test_rejoin_resume_without_snapshot_stops_before_postflash(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'rejoin',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 3
    assert [step for step, _ in calls] == ['bseed_z2m_rejoin_window.py',
                                           'bseed_z2m_metadata_refresh.py']


def test_failed_restore_blocks_postflash_candidate(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'],
                      results={'bseed_z2m_device_state.py:restore': 2})
    assert code == 2 and calls[-1][0] == 'bseed_z2m_device_state.py:restore'


def test_same_role_flash_restores_groups_on_the_moved_relay_endpoint(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path, postflash_role='Router', postflash_build='1.2.5-bseedr23')
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'flash',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 0
    assert [step for step, _ in calls] == [
        'bseed_z2m_device_state.py:snapshot', 'bseed_targeted_z2m_ota.py',
        'bseed_z2m_postota_reinterview.py', 'bseed_z2m_device_state.py:restore',
        'bseed_z2m_postflash_verify.py']
    restore = calls[3][1]
    assert restore[restore.index('--relay-endpoint') + 1] == '2'
    assert '--configure' not in restore  # same role: Z2M configures the new definition


def test_preservation_off_keeps_the_existing_flow(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    del cfg['preserve_device_state']
    code, calls = run(monkeypatch, write(tmp_path, cfg), '--mode', 'transition',
                      '--confirm-ieee', cfg['ieee'])
    assert code == 0 and not any(step.startswith('bseed_z2m_device_state') for step, _ in calls)


@pytest.mark.parametrize('changes, message', [
    ({'preserve_device_state': 'yes'}, 'explicit Boolean'),
    ({'post_ota_configure': 1}, 'explicit Boolean'),
    ({'model': 'TS0726-3-BS', 'manufacturer': '_TZ3000_other'}, 'explicitly'),
    ({'join_total_seconds': 2701}, '30..2700'),
    ({'join_total_seconds': 60, 'join_seconds': 120}, 'at least one'),
    ({'postflash_relay_endpoint': 0, 'preflash_relay_endpoint': 1}, '1..240'),
])
def test_profile_rejects_unsafe_preservation_settings(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        campaign.load_profile(write(tmp_path, base_profile(tmp_path, **changes)))


def test_explicit_relay_endpoints_override_the_socket_defaults(tmp_path):
    cfg = campaign.load_profile(write(tmp_path, base_profile(
        tmp_path, model='TS0726-3-BS', manufacturer='_TZ3000_other',
        preflash_relay_endpoint=1, postflash_relay_endpoint=4)))
    assert campaign.relay_endpoints(cfg) == (1, 4)


def test_snapshot_state_mode_never_overwrites(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    path = write(tmp_path, cfg)
    code, calls = run(monkeypatch, path, '--mode', 'snapshot-state')
    assert code == 0 and [step for step, _ in calls] == ['bseed_z2m_device_state.py:snapshot']
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'snapshot-state'])
    with pytest.raises(SystemExit, match='never overwrite'):
        campaign.main()


def test_restore_state_mode_requires_confirmation_and_snapshot(tmp_path, monkeypatch):
    cfg = base_profile(tmp_path)
    path = write(tmp_path, cfg)
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'restore-state'])
    with pytest.raises(SystemExit, match='confirm exact IEEE'):
        campaign.main()
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode',
                                      'restore-state', '--confirm-ieee', cfg['ieee']])
    with pytest.raises(SystemExit, match='pre-OTA snapshot'):
        campaign.main()
    run(monkeypatch, path, '--mode', 'snapshot-state')
    code, calls = run(monkeypatch, path, '--mode', 'restore-state', '--confirm-ieee', cfg['ieee'])
    assert code == 0 and [step for step, _ in calls] == ['bseed_z2m_device_state.py:restore']
