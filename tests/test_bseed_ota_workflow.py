"""Offline operator workflow regression tests; no MQTT or physical OTA."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

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


def test_failed_lock_needs_explicit_ack_but_remains_immutable(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    old = write_lock(root, 'campaign-first')
    original = old.read_bytes()
    with pytest.raises(ValueError, match='Previous failed OTA'):
        workflow.inspect_locks(p, root)
    assert workflow.inspect_locks(p, root, acknowledge_failures=True) == [str(old)]
    assert old.read_bytes() == original


def test_live_or_unconfirmed_lock_always_blocks(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    for phase in ('ota_running', 'update_timeout_or_unconfirmed',
                  'ota_transfer_ok_postflash_unverified', 'update_ok'):
        lock = write_lock(root, 'campaign-sibling', phase=phase)
        with pytest.raises(ValueError, match='Unresolved OTA'):
            workflow.inspect_locks(p, root, acknowledge_failures=True)
        lock.unlink()


def test_current_failed_campaign_cannot_be_renewed_by_ack(tmp_path):
    p = profile(tmp_path)
    root = Path(p['workdir']).parent
    write_lock(root, 'campaign-third')
    with pytest.raises(ValueError, match='This campaign already has a lock'):
        workflow.inspect_locks(p, root, acknowledge_failures=True)


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
