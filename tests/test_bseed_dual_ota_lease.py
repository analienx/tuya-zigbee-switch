"""Opt-in pairwise coordinator OTA leases, never a blanket concurrent gate."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper_scripts"))
from bseed_network_campaign_lock import (  # noqa: E402
    acquire, authority_path, dual_slot_path, read_lock, release_software_verified,
    release_preflight_abort, require_profile_authority, update,
)

NET = "0x00124b000000abcd"
A = "0xa4c1380000000001"
B = "0xa4c1380000000002"
C = "0xa4c1380000000003"


def p(tmp_path, ieee=A, peer=B, maxslots=2):
    return {
        "network_lock_dir": str(tmp_path / "shared"),
        "network_id": NET,
        "network_lock_shared": True,
        "workdir": str(tmp_path / ("work_" + ieee[-3:])),
        "device": "Socket" + ieee[-1],
        "ieee": ieee,
        "preflash_role": "Router",
        "postflash_role": "Router",
        "force_test_transition": False,
        "parallel_ota_max": maxslots,
        "concurrent_ota_ieees": [peer],
        "source_rejoin_strategy": "none",
        "allow_join_all_fallback": False,
        "block_bytes": 32,
        "response_delay_ms": 1800,
    }


def claim(profile, *, token=None):
    return acquire(
        require_profile_authority(profile), network_id=NET,
        token=token or profile["ieee"], device=profile["device"],
        ieee=profile["ieee"], image_sha256="1" * 64,
        parallel_allowed_ieees=profile["concurrent_ota_ieees"]
        if profile["parallel_ota_max"] == 2 else None,
    )


def test_two_exactly_reciprocal_devices_can_own_independent_slots(tmp_path):
    pa, pb = p(tmp_path), p(tmp_path, B, A)
    path_a = require_profile_authority(pa)
    path_b = require_profile_authority(pb)
    assert path_a != path_b
    assert path_a.parent == path_b.parent
    assert path_a == dual_slot_path(authority_path(pa["network_lock_dir"], NET), A)
    assert claim(pa)["parallel_ota_max"] == 2
    update(path_a, A, "ota_running")
    assert claim(pb)["parallel_ota_max"] == 2
    assert read_lock(path_a)["token"] == A
    assert read_lock(path_b)["token"] == B
    update(path_a, A, "ota_transfer_ok_postflash_unverified")
    assert read_lock(path_b)["phase"] == "preflight_complete"
    with pytest.raises(RuntimeError, match="may not release"):
        release_software_verified(path_a, A)
    update(path_a, A, "postflash_software_verified_network_released")
    assert release_software_verified(path_a, A)
    assert not path_a.exists() and path_b.exists()
    update(path_b, B, "preflight_abort")
    assert release_preflight_abort(path_b, B)
    assert not path_b.exists()


def test_missing_or_nonreciprocal_peer_refused_without_consuming_second_slot(tmp_path):
    pa = p(tmp_path)
    claim(pa)
    bad = p(tmp_path, B, C)
    with pytest.raises(RuntimeError, match="reciprocal"):
        claim(bad)
    assert read_lock(require_profile_authority(pa))["token"] == A
    assert not require_profile_authority(bad).exists()
    with pytest.raises(RuntimeError, match="already owns a parallel OTA slot"):
        claim(pa)
    assert read_lock(require_profile_authority(pa))["token"] == A


def test_second_slot_needs_active_first_transfer(tmp_path):
    first, second = p(tmp_path), p(tmp_path, B, A)
    claim(first)
    with pytest.raises(RuntimeError, match="actively updating"):
        claim(second)
    update(require_profile_authority(first), A, "ota_running")
    assert claim(second)["ieee"] == B


def test_terminal_first_transfer_does_not_authorize_late_second_slot(tmp_path):
    first, second = p(tmp_path), p(tmp_path, B, A)
    claim(first)
    update(require_profile_authority(first), A, "ota_transfer_ok_postflash_unverified")
    with pytest.raises(RuntimeError, match="actively updating"):
        claim(second)


def test_update_cannot_resurrect_or_overwrite_released_slot(tmp_path):
    first = p(tmp_path)
    path = require_profile_authority(first)
    claim(first)
    update(path, A, "postflash_software_verified_network_released")
    assert release_software_verified(path, A)
    with pytest.raises(RuntimeError, match="disappeared"):
        update(path, A, "ota_running")
    assert not path.exists()


def test_third_device_refused_even_if_names_allowlisted(tmp_path):
    a, b, c = p(tmp_path), p(tmp_path, B, A), p(tmp_path, C, A)
    claim(a)
    update(require_profile_authority(a), A, "ota_running")
    claim(b)
    with pytest.raises(RuntimeError, match="Both parallel"):
        claim(c)
    assert not require_profile_authority(c).exists()


def test_live_legacy_single_owner_blocks_dual_and_dual_blocks_single(tmp_path):
    legacy = p(tmp_path, maxslots=1)
    old = require_profile_authority(legacy)
    claim(legacy)
    with pytest.raises(RuntimeError, match="Legacy network OTA ownership"):
        claim(p(tmp_path))
    update(old, A, "preflight_abort")
    assert release_preflight_abort(old, A)
    dual = p(tmp_path)
    claim(dual)
    with pytest.raises(RuntimeError, match="Two-slot OTA ownership"):
        claim(legacy)


@pytest.mark.parametrize("profile_changes", [
    {"parallel_ota_max": True},
    {"parallel_ota_max": 3},
    {"parallel_ota_max": "2"},
    {"concurrent_ota_ieees": []},
    {"concurrent_ota_ieees": [B, C]},
    {"concurrent_ota_ieees": [A]},
    {"concurrent_ota_ieees": ["not-ieee"]},
    {"preflash_role": "Router", "postflash_role": "EndDevice"},
    {"force_test_transition": True},
    {"source_rejoin_strategy": "auto"},
    {"allow_join_all_fallback": True},
    {"block_bytes": 64},
    {"block_bytes": 0},
    {"response_delay_ms": 1200},
    {"response_delay_ms": 1500},
    {"response_delay_ms": True},
])
def test_invalid_pair_profiles_fail_before_admission(tmp_path, profile_changes):
    bad = p(tmp_path)
    bad.update(profile_changes)
    with pytest.raises(ValueError):
        require_profile_authority(bad)


def test_multi_threaded_race_never_overallocates_two_slots(tmp_path):
    pairs = [("0xa4c13800000000%02x" % i, "0xa4c13800000000%02x" % (i ^ 1)) for i in range(2, 14)]
    start = threading.Barrier(len(pairs))
    def runner(pair):
        ieee, peer = pair
        spec = p(tmp_path, ieee, peer)
        start.wait(timeout=8)
        try:
            claim(spec)
            return (True, ieee)
        except RuntimeError:
            return (False, ieee)
    with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
        records = list(pool.map(runner, pairs))
    successes = [ieee for succeeded, ieee in records if succeeded]
    assert 1 <= len(successes) <= 2
    base = authority_path(p(tmp_path)["network_lock_dir"], NET)
    on_disk = list(base.parent.glob(base.stem + ".slot-*.json"))
    assert len(on_disk) == len(successes)
    assert sorted(read_lock(s)["ieee"] for s in on_disk) == sorted(successes)


def test_no_stale_lock_for_single_owner_when_no_pair_mode(tmp_path):
    spec = p(tmp_path, maxslots=1)
    path = require_profile_authority(spec)
    claim(spec)
    assert path == authority_path(spec["network_lock_dir"], NET)
    assert "parallel_ota_max" not in read_lock(path)


def test_same_target_is_not_a_peer_and_strict_ieee_path(tmp_path):
    spec = p(tmp_path, peer=A)
    with pytest.raises(ValueError):
        require_profile_authority(spec)
    with pytest.raises(ValueError):
        dual_slot_path(tmp_path / "legacy.json", "not-an-ieee")


def test_terminal_update_release_race_never_resurrects_owner(tmp_path):
    """Race a final metadata write with release, repeated to exercise both orders."""
    for i in range(24):
        spec = p(tmp_path / str(i))
        path = require_profile_authority(spec)
        claim(spec)
        update(path, A, "preflight_abort")
        def writer():
            try:
                update(path, A, "preflight_abort", late_note="postflight evidence")
                return True
            except RuntimeError as error:
                assert "disappeared" in str(error)
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            updated = executor.submit(writer)
            released = executor.submit(release_preflight_abort, path, A)
            assert released.result(timeout=8) is True
            assert updated.result(timeout=8) in (True, False)
        assert not path.exists(), "An update resurrected a released network lease"


def test_legacy_arbitrary_json_network_lock_names_still_supported(tmp_path):
    # Existing source reconciliation and closure tests used these paths;
    # refusing them caused 9 Linux CI regressions even though live is canonical.
    for name in ('network-lock.json', 'network.json'):
        path = tmp_path / name
        acquire(path, network_id=NET, token=name, device='LegacySocket',
                ieee=A, image_sha256='2' * 64)
        update(path, name, 'preflight_abort')
        assert release_preflight_abort(path, name)
        assert not path.exists()
    with pytest.raises(ValueError, match='valid singleton'):
        acquire(tmp_path / 'network.slot-not-an-ieee.json',
                network_id=NET, token='bad', device='BadSlot',
                ieee=A, image_sha256='2' * 64)
