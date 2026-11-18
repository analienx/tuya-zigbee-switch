"""Exact WRSocketEntrance r19 -> r20 operator-risk grant. GitHub-hosted CI only."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_nonpm_recovery_gate import verify_recovery, verify_transition_recovery  # noqa: E402

BASE = {
    'device': 'WRSocketEntrance', 'ieee': '0xa4c1387b13e9436d',
    'manufacturer': 'o1jzcxou', 'model': 'TS011F-BS',
    'preflash_role': 'Router', 'postflash_role': 'Router',
    'preflash_build': '1.1.3-bseedr19', 'postflash_build': '1.1.3-bseedr20',
    'sha256': 'c3d4f3076710e390c485f5b475aa3a6ca51f444ab8fbac8a32b510f2238653a6',
    'block_bytes': 48, 'response_delay_ms': 1200,
    'preflash_relay_physical_mode': 'follow_state', 'expect_relay': 'OFF',
    'relay_get_key': 'state_relay', 'require_pm': False, 'non_pm': True,
    'force_test_transition': False,
}


def test_exact_grant_passes_only_with_load_proven_off():
    result = verify_recovery(BASE, confirm_unloaded=True)
    assert result['operator_risk_accepted'] is True
    assert result['recovery_available'] is False and result['readback_verified'] is False
    assert result['hardware_acceptance'] is False
    assert verify_recovery(dict(BASE, block_bytes=32), confirm_unloaded=True)['operator_risk_accepted']
    with pytest.raises(ValueError, match='Physical load'):
        verify_recovery(BASE, confirm_unloaded=False)


@pytest.mark.parametrize('field, value', [
    ('device', 'WRSocketWindowRightL'), ('ieee', '0xa4c1387b13e9436e'),
    ('preflash_build', '1.1.3-bseedr18'), ('postflash_build', '1.1.3-bseedr21'),
    ('sha256', '0' * 64), ('preflash_relay_physical_mode', 'power_on'),
    ('expect_relay', 'ON'), ('relay_get_key', 'state'), ('require_pm', True),
    ('force_test_transition', True), ('parallel_ota_max', 2),
    ('concurrent_ota_ieees', ['0xa4c138c5f07ee732']), ('block_bytes', 64),
    ('response_delay_ms', 0), ('manufacturer', 'b28wrpvx'),
    ('postflash_role', 'EndDevice'),
])
def test_any_other_ota_is_not_authorized(field, value):
    with pytest.raises((ValueError, TypeError)):
        verify_recovery(dict(BASE, **{field: value}), confirm_unloaded=True)


def test_never_a_role_transition():
    with pytest.raises(ValueError):
        verify_transition_recovery(dict(BASE, postflash_role='EndDevice'), confirm_unloaded=True)
