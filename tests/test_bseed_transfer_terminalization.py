"""Completed-transfer terminalization: fail-closed, crash and replay regression tests."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_transfer_terminalization as t
from bseed_network_campaign_lock import acquire, read_lock, update
from bseed_network_mutex import pending_path

NOW = dt.datetime(2026, 10, 11, 15, 0, tzinfo=dt.timezone.utc)


def iso(seconds):
    return (NOW + dt.timedelta(seconds=seconds)).isoformat()


def make_case(tmp_path):
    img = tmp_path / 'client.ota'
    img.write_bytes(b'sealed-candidate-fixture')
    work = tmp_path / 'hifi-work'
    work.mkdir()
    profile = {
        'device': 'HifiTest', 'ieee': '0x00124b0000000001',
        'network_id': 'example-zigbee', 'sha256': hashlib.sha256(img.read_bytes()).hexdigest(),
        'image': str(img), 'workdir': str(work),
        'postflash_build': 'cli14', 'postflash_role': 'EndDevice',
        'require_pm': True,
    }
    token = 'bseed-ota-fixed-test'
    net = tmp_path / 'network-authority.json'
    acquire(net, network_id=profile['network_id'], token=token,
            device=profile['device'], ieee=profile['ieee'],
            image_sha256=profile['sha256'])
    update(net, token, 'ota_transfer_ok_postflash_unverified', completed=iso(-800))
    work_lock = {
        'device': profile['device'], 'ieee': profile['ieee'],
        'sha256': profile['sha256'], 'token': token,
        'phase': 'ota_transfer_ok_postflash_unverified',
        'preflash_state': {'energy': 0.75, 'state_relay': 'OFF'},
        'response': {
            'transaction': token, 'status': 'ok',
            'data': {'id': profile['ieee'], 'to': {'software_build_id': 'cli14'}},
        }
    }
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(work_lock))
    quiet = {
        'source': t.QUIET_SOURCE, 'network_id': profile['network_id'],
        'window_started_at': iso(-140), 'window_ended_at': iso(-20),
        'bridge_online': True, 'permit_join': False,
        'complete_device_inventory': True, 'inventory_count': 107,
        'active_ota_scan_complete': True, 'active_ota_transfers': [],
        'target': {
            'ieee': profile['ieee'], 'name': profile['device'], 'build': 'cli14',
            'role': 'EndDevice', 'interview_completed': True,
        },
    }
    post = {
        'target_ieee': profile['ieee'], 'target_name': profile['device'],
        'expected_role': 'EndDevice', 'expected_build': 'cli14',
        'result': 'unconfirmed', 'fresh_mqtt_state_observed': True,
        'inventory': {
            'ieee_address': profile['ieee'],
            'software_build_id': 'cli14', 'type': 'EndDevice',
        },
        'state': {'energy': 0, 'state_relay': 'ON'},
        'issues': ['Cumulative PM energy dropped', 'Relay state changed'],
        'errors': [],
    }
    approval = {
        'action': t.ACTION, 'operator': 'test-operator',
        'approved_at': iso(-30), 'network_id': profile['network_id'],
        'ieee': profile['ieee'], 'token': token,
        'image_sha256': profile['sha256'],
    }
    return profile, net, quiet, post, approval


def test_read_only_diagnosis_preserves_energy_and_relay_failures(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    before = net.read_bytes()
    record = t.inspect(p, net, q, post, approval, now=NOW)
    assert record['hardware_acceptance'] == 'unverified'
    assert record['physical_acceptance'] is False
    assert record['original_preflash_state']['energy'] == 0.75
    assert record['observed_postflash_state']['energy'] == 0
    assert record['original_preflash_state']['state_relay'] == 'OFF'
    assert record['observed_postflash_state']['state_relay'] == 'ON'
    assert len(record['postflash_findings']) == 2
    assert net.read_bytes() == before
    assert read_lock(net)['phase'] == 'ota_transfer_ok_postflash_unverified'


@pytest.mark.parametrize('mutation', [
    ('q', 'bridge_online', False), ('q', 'permit_join', True),
    ('q', 'active_ota_transfers', ['unrelated-active-ota']),
    ('q', 'active_ota_scan_complete', False),
    ('q', 'complete_device_inventory', False),
    ('q', 'inventory_count', 0),
    ('q', 'window_ended_at', iso(-500)),
    ('q', 'window_started_at', iso(-45)),
    ('q', 'network_id', 'other-network'),
    ('approval', 'token', 'different-token'),
    ('approval', 'action', 'pretend-accepted'),
    ('approval', 'approved_at', iso(-7200)),
    ('post', 'target_ieee', '0x00124b0000000099'),
    ('post', 'fresh_mqtt_state_observed', False),
])
def test_refuse_missing_wrong_or_stale_evidence(tmp_path, mutation):
    p, net, q, post, approval = make_case(tmp_path)
    obj = {'q': q, 'approval': approval, 'post': post}[mutation[0]]
    obj[mutation[1]] = mutation[2]
    with pytest.raises(ValueError):
        t.inspect(p, net, q, post, approval, now=NOW)
    assert net.exists()


@pytest.mark.parametrize('bad_phase', [
    'ota_running', 'preflight_complete', 'update_error',
    'update_timeout_or_unconfirmed', 'preflight_abort',
    'postflash_accepted',
])
def test_unknown_or_uncompleted_transfer_never_releases(tmp_path, bad_phase):
    p, net, q, post, approval = make_case(tmp_path)
    update(net, approval['token'], bad_phase)
    with pytest.raises(ValueError, match='Only completed'):
        t.inspect(p, net, q, post, approval, now=NOW)
    assert net.exists()


def test_guard_prevents_terminalization_for_active_quarantine(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    guard = Path(p['workdir']) / 'PM_TELEMETRY_GUARD.json'
    guard.write_text('{}')
    with pytest.raises(ValueError, match='quarantine'):
        t.inspect(p, net, q, post, approval, now=NOW)


def test_commit_archives_exact_bytes_and_leaves_immutable_failure_ledger(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    original = net.read_bytes()
    receipt = t.commit(p, net, q, post, approval,
                       evidence_sha256={'quiet': 'a' * 64}, now=NOW)
    artifacts = t.artifact_paths(net, approval['token'])
    assert not net.exists()
    assert not pending_path(net).exists()
    assert artifacts['archive'].read_bytes() == original
    assert receipt['network_ownership_state'] == 'released_unaccepted'
    assert receipt['hardware_acceptance'] == 'unverified'
    assert receipt['postflash_findings'] == post['issues']
    saved = json.loads(artifacts['committed'].read_text())
    assert saved['original_preflash_state']['energy'] == 0.75
    assert saved['observed_postflash_state']['energy'] == 0
    assert json.loads(artifacts['intent'].read_text())['hardware_acceptance'] == 'unverified'
    # A second, unrelated device may now take the lease; historical failure survives.
    acquire(net, network_id=p['network_id'], token='another-target',
            device='Other', ieee='0x00124b0000000002', image_sha256='b' * 64)
    assert read_lock(net)['device'] == 'Other'
    assert artifacts['committed'].exists()
    history = t.historical_unaccepted(net)
    assert len(history) == 1
    assert history[0]['archive_integrity_verified'] is True
    assert history[0]['postflash_findings'] == post['issues']


def test_failed_archive_preserves_authority_and_blocks_future_acquisition(tmp_path, monkeypatch):
    p, net, q, post, approval = make_case(tmp_path)
    def fail_rename(*args, **kwargs):
        raise OSError('simulated filesystem failure')
    monkeypatch.setattr(t.os, 'rename', fail_rename)
    with pytest.raises(OSError, match='filesystem failure'):
        t.commit(p, net, q, post, approval, evidence_sha256={}, now=NOW)
    assert net.exists()
    assert pending_path(net).exists()
    with pytest.raises(RuntimeError, match='terminalization pending'):
        acquire(net, network_id=p['network_id'], token='new',
                device='Other', ieee='0x00124b0000000002', image_sha256='b' * 64)


def test_second_commit_refused_without_corrupting_original_ledger(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    t.commit(p, net, q, post, approval, evidence_sha256={}, now=NOW)
    existing = t.artifact_paths(net, approval['token'])['committed'].read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        t.commit(p, net, q, post, approval, evidence_sha256={}, now=NOW)
    assert t.artifact_paths(net, approval['token'])['committed'].read_bytes() == existing


def test_actual_image_mutation_is_rejected(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    Path(p['image']).write_bytes(b'different')
    with pytest.raises(ValueError, match='Candidate image'):
        t.inspect(p, net, q, post, approval, now=NOW)


def test_pending_network_lease_guard_is_fail_closed(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    pending_path(net).write_text('unresolved journal')
    with pytest.raises(RuntimeError, match='terminalization pending'):
        acquire(net, network_id=p['network_id'], token='second',
                device='Other', ieee='0x00124b0000000002', image_sha256='c' * 64)


def test_historical_archive_tampering_detected(tmp_path):
    p, net, q, post, approval = make_case(tmp_path)
    t.commit(p, net, q, post, approval, evidence_sha256={}, now=NOW)
    paths = t.artifact_paths(net, approval['token'])
    paths['archive'].write_text('corrupt')
    with pytest.raises(ValueError, match='archived ownership hash'):
        t.historical_unaccepted(net)
