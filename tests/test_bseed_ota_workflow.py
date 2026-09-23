"""Offline BSEED operator regression tests: never contact MQTT or submit OTA."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_workflow as workflow


def profile(tmp_path):
    private = tmp_path / 'private'
    served = private / 'serve'
    served.mkdir(parents=True)
    image = served / 'forward.ota'
    image.write_bytes(b'firmware-image-example')
    return dict(image=str(image), index_output=str(served / 'index.json'),
                sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                url='http://127.0.0.1:8787/forward.ota',
                index_url='http://127.0.0.1:8787/index.json',
                device='BedroomSocketCabinetRight', ieee='0xa4c13824a7005afb',
                workdir=str(private / 'campaign-third'), non_pm=True)


def write_lock(root, folder, *, ieee='0xa4c13824a7005afb', phase='update_error'):
    path = root / folder / 'ACTIVE_LOCK.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'ieee': ieee, 'phase': phase}))
    return path


def test_pinned_http_paths_and_image_hash(tmp_path):
    p = profile(tmp_path)
    workflow.image_contract(p)
    p['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='checksum'):
        workflow.image_contract(p)
    p['sha256'] = hashlib.sha256(Path(p['image']).read_bytes()).hexdigest()
    p['index_url'] = 'http://127.0.0.1:8787/unrelated.json'
    with pytest.raises(ValueError, match='filename'):
        workflow.image_contract(p)
    p['index_url'] = 'http://other-host:8787/index.json'
    with pytest.raises(ValueError, match='same private'):
        workflow.image_contract(p)


def test_failed_lock_needs_ack_but_remains_immutable(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    old = write_lock(root, 'campaign-first')
    original = old.read_bytes()
    with pytest.raises(ValueError, match='Previous failed OTA'):
        workflow.inspect_locks(p, root)
    assert workflow.inspect_locks(p, root, acknowledge_failures=True) == [str(old)]
    assert old.read_bytes() == original


def test_active_or_unconfirmed_same_target_always_blocks(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    for phase in ('ota_running', 'update_timeout_or_unconfirmed',
                  'ota_transfer_ok_postflash_unverified', 'update_ok'):
        lock = write_lock(root, 'campaign-sibling', phase=phase)
        with pytest.raises(ValueError, match='active OTA|Unverified previous'):
            workflow.inspect_locks(p, root, acknowledge_failures=True)
        lock.unlink()


def test_unverified_other_device_does_not_claim_active_transfer(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    other = '0xa4c13824a7005abc'
    lock = write_lock(root, 'other-finished', ieee=other,
                      phase='ota_transfer_ok_postflash_unverified')
    assert workflow.inspect_locks(p, root) == []
    assert lock.exists()
    live = write_lock(root, 'other-running', ieee=other, phase='ota_running')
    with pytest.raises(ValueError, match='active OTA'):
        workflow.inspect_locks(p, root, acknowledge_failures=True)
    assert live.exists()


def test_audit_reports_historical_and_running_without_waiver(tmp_path, capsys):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    write_lock(root, 'campaign-first', phase='update_error')
    write_lock(root, 'campaign-live', phase='ota_running')
    assert workflow.inspect_locks(p, root, audit=True) == []
    out = capsys.readouterr().out
    assert 'CAMPAIGN_LOCK' in out and 'update_error' in out and 'ota_running' in out


def test_same_workdir_cannot_be_reused_even_if_accepted(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    for phase in ('update_error', 'postflash_accepted'):
        current = write_lock(root, 'campaign-third', phase=phase)
        with pytest.raises(ValueError, match='Reuse of a campaign workdir'):
            workflow.inspect_locks(p, root, acknowledge_failures=True)
        current.unlink()


def test_corrupt_history_fails_closed(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    lock = write_lock(root, 'old')
    lock.write_text('{broken')
    with pytest.raises(ValueError, match='Unparseable'):
        workflow.inspect_locks(p, root, acknowledge_failures=True)


def test_qualify_is_serial_and_never_flashes(tmp_path, monkeypatch):
    p = profile(tmp_path)
    stages = []
    monkeypatch.setattr(workflow, 'make_index', lambda p: stages.append('prepare'))
    monkeypatch.setattr(workflow, 'check_served', lambda p: stages.append('http_checked'))
    monkeypatch.setattr(workflow, 'campaign', lambda _path, mode, **kwargs: stages.append(mode))
    workflow.ready(tmp_path / 'private' / 'profile.json', p,
                   Path(p['workdir']).parent, False)
    assert stages == ['prepare', 'http_checked', 'preflight', 'qualify', 'http_checked']
    assert 'flash' not in stages


def test_pm_profile_uses_existing_check_not_nonpm_qualify(tmp_path, monkeypatch):
    p = profile(tmp_path)
    p['non_pm'] = False
    stages = []
    monkeypatch.setattr(workflow, 'make_index', lambda p: None)
    monkeypatch.setattr(workflow, 'check_served', lambda p: None)
    monkeypatch.setattr(workflow, 'campaign', lambda _path, mode, **kwargs: stages.append(mode))
    workflow.ready(tmp_path / 'private' / 'profile.json', p,
                   Path(p['workdir']).parent, False)
    assert stages == ['preflight', 'check']
