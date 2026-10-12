"""Failed OTA abandonment releases only the network lease, never acceptance."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

import bseed_abandon_failed_campaign as abandon
from bseed_network_campaign_lock import (
    ABANDONED_PHASE,
    acquire,
    read_lock,
    release_abandoned,
    require_profile_authority,
    update,
)
from bseed_targeted_z2m_ota import new_campaign_allowed


def profile(tmp_path):
    image = tmp_path / 'candidate.ota'
    image.write_bytes(b'candidate')
    return {
        'device': 'SocketA',
        'ieee': '0x00124b0000000001',
        'manufacturer': 'o1jzcxou',
        'model': 'TS011F-BS',
        'preflash_role': 'EndDevice',
        'preflash_build': '1.1.3-source',
        'postflash_role': 'EndDevice',
        'postflash_build': '1.1.3-next',
        'image': str(image),
        'sha256': 'a' * 64,
        'url': 'http://example.invalid/fw.ota',
        'mqtt_config': str(tmp_path / 'mqtt.yaml'),
        'broker': '127.0.0.1',
        'workdir': str(tmp_path / 'work'),
        'manufacturer_code': 4417,
        'image_type': 65026,
        'file_version': '0x11023020',
        'expect_relay': 'OFF',
        'index_url': 'http://example.invalid/index.json',
        'index_output': str(tmp_path / 'index.json'),
        'template_index': str(tmp_path / 'template.json'),
        'network_lock_dir': str(tmp_path / 'network'),
        'network_id': 'zigbee-test-network',
        'network_lock_shared': True,
        'require_pm': False,
        'non_pm': True,
        'preflash_relay_physical_mode': 'follow_state',
    }


def pending_fixture(tmp_path):
    p = profile(tmp_path)
    work = Path(p['workdir'])
    work.mkdir()
    token = 'token-one'
    source_path = work / 'source_unchanged_test.json'
    source = {
        'result': abandon.PENDING_PHASE,
        'at': '2026-10-12T08:00:00+00:00',
        'ieee': p['ieee'],
        'device': p['device'],
        'candidate_sha256': p['sha256'],
        'source_build': p['preflash_build'],
        'source_role': p['preflash_role'],
        'fresh_get_ieee_verified': True,
        'fresh_get_build_role_verified': True,
        'candidate_verification_deferred': True,
        'ota_transport_success': False,
        'hardware_acceptance': False,
    }
    source_path.write_text(json.dumps(source))
    work_lock = {
        'device': p['device'], 'ieee': p['ieee'], 'sha256': p['sha256'],
        'phase': abandon.PENDING_PHASE, 'token': token,
        'reconciliation_evidence': str(source_path),
    }
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(work_lock))
    network_path = require_profile_authority(p)
    acquire(
        network_path, network_id=p['network_id'], token=token,
        device=p['device'], ieee=p['ieee'], image_sha256=p['sha256'],
    )
    update(network_path, token, abandon.PENDING_PHASE,
           reconciliation_evidence=str(source_path))
    return p, work, network_path


def test_abandon_releases_network_but_old_campaign_stays_terminal(tmp_path):
    p, work, network_path = pending_fixture(tmp_path)
    evidence = abandon.finalize_abandonment(p, reason='candidate_unresponsive')

    assert evidence.is_file()
    assert not network_path.exists()
    lock = json.loads((work / 'ACTIVE_LOCK.json').read_text())
    assert lock['phase'] == ABANDONED_PHASE
    assert lock['hardware_acceptance'] is False
    assert lock['candidate_retry_authorized'] is False
    assert new_campaign_allowed(lock) is False

    record = json.loads(evidence.read_text())
    assert record['source_build'] == p['preflash_build']
    assert record['source_role'] == p['preflash_role']
    assert record['hardware_acceptance'] is False
    assert record['old_campaign_reusable'] is False


def test_abandon_refuses_mismatched_network_authority(tmp_path):
    p, work, network_path = pending_fixture(tmp_path)
    network = read_lock(network_path)
    network['image_sha256'] = 'b' * 64
    network_path.write_text(json.dumps(network))

    with pytest.raises(RuntimeError, match='network authority'):
        abandon.finalize_abandonment(p, reason='operator_abandoned')
    assert network_path.exists()
    assert json.loads((work / 'ACTIVE_LOCK.json').read_text())['phase'] == abandon.PENDING_PHASE


def test_release_abandoned_is_phase_and_token_scoped(tmp_path):
    p, _work, path = pending_fixture(tmp_path)
    with pytest.raises(RuntimeError):
        release_abandoned(path, 'token-one')
    update(path, 'token-one', ABANDONED_PHASE)
    with pytest.raises(RuntimeError):
        release_abandoned(path, 'wrong-token')
    assert path.exists()
    assert release_abandoned(path, 'token-one')
    assert not path.exists()
