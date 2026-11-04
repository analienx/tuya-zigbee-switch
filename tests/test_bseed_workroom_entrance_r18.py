"""Pinned non-PM Router preflight and recovery, with no fleet-wide gate bypass."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
from bseed_nonpm_relay_policy import (
    exact_same_role_nonpm_router_admission, is_exact_entrance_router_candidate,
    ENTRANCE_IDENTITY, BALCONY_IDENTITY, BALCONY_LEGACY_POLICY,
)
from bseed_nonpm_recovery_gate import verify_recovery
from bseed_targeted_z2m_ota import validate_metering_preflight


def candidate():
    p = dict(ENTRANCE_IDENTITY)
    p.update(non_pm=True, require_pm=False, expect_relay="OFF",
             relay_get_key="state_relay", preflash_relay_physical_mode="follow_state",
             block_bytes=32, force_test_transition=False)
    return p


def test_entrance_exact_route_is_meterless_and_unloaded_operator_accepted():
    p = candidate()
    assert is_exact_entrance_router_candidate(p)
    assert exact_same_role_nonpm_router_admission(p)
    assert verify_recovery(p, confirm_unloaded=True)["recovery_available"] is False
    assert validate_metering_preflight({"state_relay": "OFF"},
        non_pm=True, manufacturer=p["manufacturer"], model=p["model"],
        role=p["preflash_role"], max_reported_watts=1,
        nonpm_router_transition=exact_same_role_nonpm_router_admission(p)) is None


def test_entrance_refuses_missing_unloaded_confirmation():
    with pytest.raises(ValueError, match="Physical load"):
        verify_recovery(candidate(), confirm_unloaded=False)


@pytest.mark.parametrize("field,value", [
    ("device", "BedroomSocketBalcony"), ("ieee", "0xa4c138ac993cc245"),
    ("manufacturer", "b28wrpvx"), ("model", "TS011F-BS-PM"),
    ("preflash_role", "EndDevice"), ("postflash_role", "EndDevice"),
    ("preflash_build", "1.1.3-bseedr17"), ("postflash_build", "1.1.3-bseedr19"),
    ("sha256", "0"*64), ("non_pm", False), ("require_pm", True),
    ("expect_relay", "ON"), ("relay_get_key", "state"),
    ("preflash_relay_physical_mode", "legacy_unreported_v8_router"),
    ("block_bytes", 48), ("force_test_transition", True),
])
def test_entrance_exception_cannot_be_used_for_other_hardware_or_images(field, value):
    p = candidate()
    p[field] = value
    assert not exact_same_role_nonpm_router_admission(p) or not (
        p.get("preflash_relay_physical_mode") == "follow_state")
    with pytest.raises((ValueError, AssertionError)):
        verify_recovery(p, confirm_unloaded=True)


def test_cannot_use_generic_router_to_disable_metering_gate():
    p = candidate()
    assert validate_metering_preflight({"state_relay":"OFF"},non_pm=True,
        model=p["model"],manufacturer=p["manufacturer"],role="Router",
        max_reported_watts=1,nonpm_router_transition=True) is None
    with pytest.raises(AssertionError,match="exception only"):
        validate_metering_preflight({"state_relay":"OFF"},non_pm=True,
            model=p["model"],manufacturer=p["manufacturer"],role="Router",
            max_reported_watts=1,nonpm_router_transition=False)
    with pytest.raises(AssertionError, match="unexpectedly exposes PM"):
        validate_metering_preflight({"state_relay":"OFF","power":0},non_pm=True,
            model=p["model"],manufacturer=p["manufacturer"],role="Router",
            max_reported_watts=1,nonpm_router_transition=True)


def test_balcony_legacy_policy_admission_remains_exact():
    p = dict(BALCONY_IDENTITY, non_pm=True,require_pm=False,
             expect_relay="OFF",relay_get_key="state_relay",block_bytes=32,
             preflash_relay_physical_mode=BALCONY_LEGACY_POLICY,force_test_transition=False)
    assert exact_same_role_nonpm_router_admission(p)
    assert verify_recovery(p,confirm_unloaded=True)["recovery_available"] is False
