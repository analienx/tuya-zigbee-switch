"""OTA never intentionally disconnects appliances: relay state preservation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_relay_state_policy as p


def profile(**overrides):
    baseline = dict(
        relay_state_policy="preserve",
        auto_relay_off=False,
        expect_relay="ON",
        relay_get_key="state_relay",
        preflash_role="Router",
        postflash_role="Router",
        require_pm=True,
        non_pm=False,
        pm_preflash_load_proof="meter",
        max_reported_watts=100.0,
        device="FixtureSocket",
        ieee="0xa4c138aed67ea773",
    )
    baseline.update(overrides)
    return baseline


@pytest.mark.parametrize("state", ["ON", "OFF"])
def test_both_existing_relay_states_allowed(state):
    assert p.validate(profile(expect_relay=state)) is True


@pytest.mark.parametrize("bad", [
    {"auto_relay_off": True},
    {"auto_relay_off": None},
    {"expect_relay": "UNKNOWN"},
    {"relay_get_key": "relay"},
    {"max_reported_watts": 101},
    {"max_reported_watts": -1},
    {"max_reported_watts": float("nan")},
    {"max_reported_watts": True},
    {"pm_preflash_load_proof": "physically_unloaded"},
    {"require_pm": False},
    {"preflash_role": "EndDevice"},
])
def test_fail_closed_if_policy_unverifiable(bad):
    with pytest.raises(ValueError):
        p.validate(profile(**bad))


def test_state_check_never_sends_relay_command():
    class ReadOnly:
        def __init__(self, actual):
            self.actual = actual
            self.calls = []
        def read(self, key):
            self.calls.append(("read", key))
            return self.actual
        def set(self, *args):
            raise AssertionError("Relay write must never be sent")

    reader = ReadOnly("ON")
    assert p.verify_after_flash(profile(), reader) == "ON"
    assert reader.calls == [("read", "state_relay")]
    reader = ReadOnly("OFF")
    with pytest.raises(ValueError, match="Postflash relay changed"):
        p.verify_after_flash(profile(), reader)
    assert reader.calls == [("read", "state_relay")]


def test_historical_profile_still_readable():
    assert p.validate({"device": "legacy"}) is False
    assert p.verify_after_flash({"device": "legacy"}) is None
