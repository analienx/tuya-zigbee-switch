"""Network-wide OTA ownership is independent of campaign workdirs."""

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

from bseed_network_campaign_lock import (
    acquire,
    authority_path,
    read_lock,
    release_accepted,
    release_preflight_abort,
    release_reconciled,
    release_source_unchanged,
    require_profile_authority,
    update,
)


def profile(tmp_path, work_name):
    return {
        'workdir': str(tmp_path / work_name),
        'network_lock_dir': str(tmp_path / 'shared-network-authority'),
        'network_id': 'zigbee-pan-00124b0001abcdef',
        'network_lock_shared': True,
    }


def test_two_workdirs_and_targets_share_one_atomic_network_owner(tmp_path):
    first = profile(tmp_path, 'work-a')
    second = profile(tmp_path, 'work-b')
    path_a = require_profile_authority(first)
    path_b = require_profile_authority(second)
    assert path_a == path_b
    assert path_a != Path(first['workdir']) / 'ACTIVE_LOCK.json'

    acquire(path_a, network_id=first['network_id'], token='one',
            device='SocketA', ieee='0x00124b0000000001',
            image_sha256='a' * 64)
    with pytest.raises(RuntimeError, match='already has campaign ownership'):
        acquire(path_b, network_id=second['network_id'], token='two',
                device='SocketB', ieee='0x00124b0000000002',
                image_sha256='b' * 64)

    record = read_lock(path_a)
    assert record['token'] == 'one'
    assert record['device'] == 'SocketA'


def test_failed_or_unverified_campaign_retains_network_ownership(tmp_path):
    p = profile(tmp_path, 'work-a')
    path = require_profile_authority(p)
    acquire(path, network_id=p['network_id'], token='one',
            device='SocketA', ieee='0x00124b0000000001',
            image_sha256='a' * 64)

    for phase in ('ota_running', 'update_error',
                  'update_timeout_or_unconfirmed',
                  'ota_transfer_ok_postflash_unverified'):
        update(path, 'one', phase)
        with pytest.raises(RuntimeError):
            release_accepted(path, 'one')
        assert path.exists()

    update(path, 'one', 'postflash_accepted')
    assert release_accepted(path, 'one')
    assert not path.exists()


def test_installed_image_reconciled_has_its_own_nonacceptance_release(tmp_path):
    p = profile(tmp_path, 'work-a')
    path = require_profile_authority(p)
    acquire(path, network_id=p['network_id'], token='one',
            device='SocketA', ieee='0x00124b0000000001',
            image_sha256='a' * 64)
    with pytest.raises(RuntimeError):
        release_reconciled(path, 'one')
    update(path, 'one', 'installed_image_reconciled')
    with pytest.raises(RuntimeError):
        release_accepted(path, 'one')
    assert release_reconciled(path, 'one')
    assert not path.exists()


def test_source_unchanged_reconciled_has_its_own_release(tmp_path):
    p = profile(tmp_path, 'work-a')
    path = require_profile_authority(p)
    acquire(path, network_id=p['network_id'], token='one',
            device='SocketA', ieee='0x00124b0000000001',
            image_sha256='a' * 64)
    with pytest.raises(RuntimeError):
        release_source_unchanged(path, 'one')
    update(path, 'one', 'source_unchanged_reconciled')
    with pytest.raises(RuntimeError):
        release_accepted(path, 'one')
    assert release_source_unchanged(path, 'one')
    assert not path.exists()


def test_preflight_abort_is_only_nonacceptance_release(tmp_path):
    p = profile(tmp_path, 'work-a')
    path = require_profile_authority(p)
    acquire(path, network_id=p['network_id'], token='one',
            device='SocketA', ieee='0x00124b0000000001',
            image_sha256='a' * 64)
    with pytest.raises(RuntimeError):
        release_preflight_abort(path, 'one')
    update(path, 'one', 'preflight_abort')
    assert release_preflight_abort(path, 'one')
    assert not path.exists()


def test_authority_configuration_fails_closed(tmp_path):
    p = profile(tmp_path, 'work-a')
    for key in ('network_id', 'network_lock_dir'):
        bad = dict(p)
        bad.pop(key)
        with pytest.raises(ValueError):
            require_profile_authority(bad)
    bad = dict(p, network_lock_shared=False)
    with pytest.raises(ValueError, match='shared'):
        require_profile_authority(bad)
    bad = dict(p, network_lock_dir=p['workdir'])
    with pytest.raises(ValueError, match='independent'):
        require_profile_authority(bad)
