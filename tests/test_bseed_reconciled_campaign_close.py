"""Installed-image reconciliation closes ownership without claiming OTA/hardware success."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

import bseed_reconciled_campaign_close as close
from bseed_network_campaign_lock import acquire, read_lock, update


def profile(tmp_path):
    work = tmp_path / 'campaign'
    work.mkdir()
    image = tmp_path / 'candidate.ota'
    image.write_bytes(b'candidate')
    return {
        'device': 'KitchenSocketLeft',
        'ieee': '0xa4c138241e3de538',
        'image': str(image),
        'sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
        'postflash_build': '1.2.5-bseedcli12',
        'preflash_role': 'EndDevice',
        'postflash_role': 'EndDevice',
        'require_pm': True,
        'workdir': str(work),
    }


def metadata(p):
    return {
        'at': '2026-09-28T14:25:00+02:00',
        'ieee': p['ieee'],
        'device': p['device'],
        'result': 'metadata_already_correct',
        'error': None,
        'interview_ok': True,
        'fresh_inventory_observed': True,
        'live_zdo_after': {'role': 'EndDevice'},
        'after': {
            'ieee_address': p['ieee'],
            'software_build_id': p['postflash_build'],
            'type': 'EndDevice',
        },
    }


def audit(p):
    return {
        'at': '2026-09-28T14:26:00+02:00',
        'ieee': p['ieee'],
        'device': p['device'],
        'expected_role': 'EndDevice',
        'expected_build': p['postflash_build'],
        'result': 'read_only_audit_candidate',
        'issues': [],
        'writes': 0,
        'missing_reporting': [],
        'mqtt': {'messages': 3, 'span_seconds': 70.0},
    }


def postflash(p):
    return {
        'observed_at': '2026-09-28T14:29:00+02:00',
        'target_ieee': p['ieee'],
        'target_name': p['device'],
        'expected_role': 'EndDevice',
        'expected_build': p['postflash_build'],
        'result': 'unconfirmed',
        'issues': [close.EXPECTED_TIMEOUT_ISSUE],
        'pm_readiness': {'required': True, 'issues': []},
        'fresh_mqtt_state_observed': True,
        'inventory': {
            'ieee_address': p['ieee'],
            'software_build_id': p['postflash_build'],
            'type': 'EndDevice',
            'interview_completed': True,
        },
        'state': {'state_relay': 'OFF'},
        'errors': [],
    }


def write(path, data):
    path.write_text(json.dumps(data))
    return path


def test_validators_require_exact_clean_evidence(tmp_path):
    p = profile(tmp_path)
    lock = {'preflash_state': {'state_relay': 'OFF'}}
    close.validate_metadata(p, metadata(p))
    close.validate_pm_audit(p, audit(p))
    close.validate_postflash(p, postflash(p), lock)

    bad = metadata(p); bad['fresh_inventory_observed'] = False
    with pytest.raises(ValueError): close.validate_metadata(p, bad)
    bad = audit(p); bad['issues'] = ['radio error']
    with pytest.raises(ValueError): close.validate_pm_audit(p, bad)
    bad = postflash(p); bad['issues'].append('another issue')
    with pytest.raises(ValueError): close.validate_postflash(p, bad, lock)
    bad = postflash(p); bad['state']['state_relay'] = 'ON'
    with pytest.raises(ValueError): close.validate_postflash(p, bad, lock)


def test_main_releases_only_network_ownership_and_preserves_truth(tmp_path, monkeypatch):
    p = profile(tmp_path)
    work = Path(p['workdir'])
    token = 'old-timeout'
    work_lock = {
        'device': p['device'], 'ieee': p['ieee'], 'sha256': p['sha256'],
        'phase': 'update_timeout_or_unconfirmed', 'token': token,
        'preflash_state': {'state_relay': 'OFF'},
    }
    write(work / 'ACTIVE_LOCK.json', work_lock)
    write(work / ('PM_TELEMETRY_RELEASED_RECONCILED_' + token + '.json'), {
        'ieee': p['ieee'], 'sha256': p['sha256'],
        'transport_phase_preserved': 'update_timeout_or_unconfirmed',
    })

    network = tmp_path / 'network.json'
    acquire(network, network_id='network-1', token=token, device=p['device'],
            ieee=p['ieee'], image_sha256=p['sha256'])
    update(network, token, 'update_timeout_or_unconfirmed')

    meta = write(tmp_path / 'metadata.json', metadata(p))
    pm = write(tmp_path / 'audit.json', audit(p))
    post = write(tmp_path / 'postflash.json', postflash(p))
    output = tmp_path / 'closure.json'

    monkeypatch.setattr(close, 'load_profile', lambda _path: p)
    monkeypatch.setattr(close, 'network_lock_path', lambda _profile, required=False: network)
    monkeypatch.setattr(sys, 'argv', [
        'close', '--profile', str(tmp_path / 'profile.json'),
        '--confirm-ieee', p['ieee'],
        '--metadata-evidence', str(meta),
        '--pm-audit-evidence', str(pm),
        '--postflash-evidence', str(post),
        '--output', str(output),
    ])
    close.main()

    assert not network.exists()
    final_lock = json.loads((work / 'ACTIVE_LOCK.json').read_text())
    assert final_lock['phase'] == 'installed_image_reconciled'
    assert final_lock['ota_transport_success'] is False
    assert final_lock['hardware_acceptance'] is False
    record = json.loads(output.read_text())
    assert record['result'] == 'installed_image_reconciled'
    assert record['otaTransportSuccess'] is False
    assert record['hardwareAcceptance'] is False
    assert record['physicalAcceptance'] is False


def test_main_refuses_cross_role_or_non_timeout_campaign(tmp_path, monkeypatch):
    p = profile(tmp_path)
    p['postflash_role'] = 'Router'
    monkeypatch.setattr(close, 'load_profile', lambda _path: p)
    monkeypatch.setattr(sys, 'argv', [
        'close', '--profile', str(tmp_path / 'profile.json'),
        '--confirm-ieee', p['ieee'],
        '--metadata-evidence', 'x', '--pm-audit-evidence', 'x',
        '--postflash-evidence', 'x', '--output', 'x',
    ])
    with pytest.raises(ValueError, match='same-role'):
        close.main()
