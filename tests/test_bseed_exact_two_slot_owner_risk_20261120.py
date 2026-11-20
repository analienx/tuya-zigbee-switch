"""Exact two-slot owner-risk canary: identity, pairing and load proof remain fail-closed."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_canary import CanaryError, require_flash_confirmation  # noqa: E402
from bseed_nonpm_recovery_gate import (  # noqa: E402
    PARALLEL_20261120_CANDIDATES, PARALLEL_20261120_COMMON,
    verify_recovery, verify_transition_recovery,
)


def profile_for(candidate):
    return dict(PARALLEL_20261120_COMMON, **candidate)


@pytest.mark.parametrize('candidate', PARALLEL_20261120_CANDIDATES)
def test_exact_canaries_require_load_proof_but_not_unavailable_swire(candidate):
    profile = profile_for(candidate)
    with pytest.raises(ValueError, match='Physical load'):
        verify_recovery(profile, confirm_unloaded=False)
    result = verify_recovery(profile, confirm_unloaded=True)
    assert result['ieee'] == candidate['ieee']
    assert result['operator_risk_accepted'] is True
    assert result['readback_verified'] is False
    assert result['recovery_available'] is False
    assert result['hardware_acceptance'] is False
    # No human unplugging claim is made. The campaign must obtain verified
    # relay OFF through bseed_relay_auto_off BEFORE submitting any OTA request.
    require_flash_confirmation(profile, candidate['ieee'], False)


@pytest.mark.parametrize('candidate', PARALLEL_20261120_CANDIDATES)
@pytest.mark.parametrize('field,value', [
    ('owner_irreversible_risk_accepted', False),
    ('auto_relay_off', False),
    ('network_lock_shared', False),
    ('parallel_ota_max', 1),
    ('source_rejoin_strategy', 'scoped'),
    ('allow_join_all_fallback', True),
    ('block_bytes', 48),
    ('response_delay_ms', 1200),
    ('force_test_transition', True),
    ('require_pm', True),
    ('non_pm', False),
    ('manufacturer', '_TZ3000_o1jzcxou'),
    ('model', 'TS011F-BS-PM'),
    ('preflash_relay_physical_mode', 'always_on'),
    ('expect_relay', 'ON'),
    ('relay_get_key', 'state'),
    ('sha256', '0' * 64),
    ('preflash_build', '1.1.3-bseedc18'),
    ('postflash_build', '1.1.3-bseedr22'),
    ('preflash_role', 'EndDevice'),
    ('postflash_role', 'Router'),
    ('ieee', '0xa4c1380000000000'),
    ('device', 'NotAuthorized'),
    ('concurrent_ota_ieees', []),
])
def test_any_wrong_detail_fails_closed(candidate, field, value):
    # A field changed to its original value cannot constitute a deviation.
    if candidate.get(field, PARALLEL_20261120_COMMON.get(field)) == value:
        return
    candidate_profile = dict(profile_for(candidate), **{field: value})
    with pytest.raises((ValueError, TypeError, AssertionError)):
        verify_recovery(candidate_profile, confirm_unloaded=True)


@pytest.mark.parametrize('candidate', PARALLEL_20261120_CANDIDATES)
def test_wrong_peer_and_role_transition_not_admitted(candidate):
    profile = profile_for(candidate)
    with pytest.raises(ValueError):
        verify_recovery(dict(profile, concurrent_ota_ieees=[candidate['ieee']]),
                        confirm_unloaded=True)
    with pytest.raises(ValueError):
        verify_transition_recovery(dict(profile, postflash_role='EndDevice'
                                        if candidate['postflash_role'] == 'Router'
                                        else 'Router'),
                                   confirm_unloaded=True)


def test_canary_does_not_accept_unverified_flash_confirmation():
    client = profile_for(PARALLEL_20261120_CANDIDATES[1])
    with pytest.raises(CanaryError, match='confirm-ieee'):
        require_flash_confirmation(client, PARALLEL_20261120_CANDIDATES[0]['ieee'], False)
    with pytest.raises(CanaryError, match='verified automatic relay OFF'):
        require_flash_confirmation(dict(client, auto_relay_off=False), client['ieee'], False)
    with pytest.raises(CanaryError, match='verified automatic relay OFF'):
        require_flash_confirmation(dict(client, auto_relay_off='true'), client['ieee'], False)
