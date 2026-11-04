"""Exact no-backup BSEED Router v8 canary with legacy-unreported mode."""
import copy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_nonpm_relay_policy import (
    BALCONY_LEGACY_POLICY, relay_policy_matches,
    verify_relay_policy_profile,
)
from bseed_nonpm_recovery_gate import verify_recovery
from bseed_nonpm_link_gate import run as link_run


def candidate():
    return {
        'device': 'BedroomSocketBalcony',
        'ieee': '0xa4c138ac993cc245',
        'manufacturer': 'o1jzcxou',
        'model': 'TS011F-BS',
        'preflash_role': 'Router',
        'postflash_role': 'Router',
        'preflash_build': '1.1.3-bseedv8',
        'postflash_build': '1.1.3-bseedr18',
        'sha256': '4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9',
        'preflash_relay_physical_mode': BALCONY_LEGACY_POLICY,
        'expect_relay': 'OFF',
        'relay_get_key': 'state_relay',
        'block_bytes': 32,
        'non_pm': True,
        'require_pm': False,
    }


def test_exact_router_r18_operator_accepted_risk_is_not_a_recovery_backup():
    p = candidate()
    assert verify_relay_policy_profile(p) is True
    evidence = verify_recovery(p, confirm_unloaded=True)
    assert evidence['ieee'] == p['ieee']
    assert evidence['recovery_available'] is False
    assert 'Router' in evidence['method']


@pytest.mark.parametrize('field,other', [
    ('device', 'BedroomSocketCabinetRight'),
    ('ieee', '0xa4c13824a7005afb'),
    ('manufacturer', 'b28wrpvx'),
    ('model', 'TS011F'),
    ('preflash_role', 'EndDevice'),
    ('postflash_role', 'EndDevice'),
    ('preflash_build', '1.1.3-bseedr17'),
    ('postflash_build', '1.1.3-bseedr19'),
    ('sha256', '0' * 64),
    ('relay_get_key', 'state'),
    ('expect_relay', 'ON'),
    ('preflash_relay_physical_mode', 'follow_state'),
    ('require_pm', True),
    ('non_pm', False),
    ('block_bytes', 48),
])
def test_no_unsafe_generalization_of_legacy_router_exception(field, other):
    p = candidate()
    p[field] = other
    with pytest.raises((ValueError, AssertionError)):
        verify_recovery(p, confirm_unloaded=True)


def test_unloaded_confirmation_remains_mandatory():
    with pytest.raises(ValueError, match='Physical load'):
        verify_recovery(candidate(), confirm_unloaded=False)


def test_unknown_preflash_mode_requires_explicit_null_in_fresh_reply():
    p = candidate()
    assert relay_policy_matches({'relay_physical_mode': None}, p)
    assert not relay_policy_matches({}, p)
    assert not relay_policy_matches({'relay_physical_mode': 'follow_state'}, p)
    assert not relay_policy_matches({'relay_physical_mode': 'always_on'}, p)
    assert not relay_policy_matches({'relay_physical_mode': 'OFF'}, p)
    assert not relay_policy_matches(None, p)


def test_legacy_null_cannot_be_used_on_other_source_or_image():
    p = candidate()
    for change in ({'sha256': 'f'*64}, {'ieee': '0x00124b0000000000'},
                   {'preflash_build': '1.1.3-bseedr12'}):
        foreign = dict(p, **change)
        with pytest.raises(ValueError, match='restricted'):
            relay_policy_matches({'relay_physical_mode': None}, foreign)


def test_existing_client_relay_policy_behavior_unchanged():
    p = dict(candidate(), device='BedroomSocketCabinetRight',
             ieee='0xa4c13824a7005afb',
             preflash_role='EndDevice', postflash_role='EndDevice',
             preflash_relay_physical_mode='follow_state',
             preflash_build='1.1.3-bseedc9',
             postflash_build='1.1.3-bseedc16')
    assert relay_policy_matches({'relay_physical_mode':'follow_state'}, p)
    assert not relay_policy_matches({'relay_physical_mode':None},p)


def test_link_gate_admits_same_role_router_only_after_exact_profile_check():
    p=candidate()
    # Stop before any real MQTT or Zigbee operations; this verifies that
    # Router→Router reaches (but never skips) the mandatory ZCL probe hook.
    with patch('bseed_nonpm_link_gate._probe_command',
               side_effect=RuntimeError('probe_not_started')) as probe:
        with pytest.raises(RuntimeError,match='probe_not_started'):
            link_run(p)
        probe.assert_called_once()
    foreign = dict(p, sha256='0'*64)
    with patch('bseed_nonpm_link_gate._probe_command') as probe:
        with pytest.raises(ValueError, match='restricted'):
            link_run(foreign)
        probe.assert_not_called()


def test_cross_role_nonpm_still_fails_before_ota():
    p = dict(candidate(), postflash_role='EndDevice')
    with pytest.raises(ValueError, match='restricted'):
        verify_relay_policy_profile(p)


def test_explicit_swire_record_takes_priority_over_no_backup_exception(tmp_path):
    p = candidate()
    p['recovery_evidence'] = str(tmp_path / 'missing_swire_backup.json')
    with pytest.raises(ValueError, match='Recovery evidence'):
        verify_recovery(p, confirm_unloaded=True)
