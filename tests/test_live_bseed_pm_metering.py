"""Offline regression gates for the live BSEED PM measurement test runner."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from live_bseed_pm_metering import Sample, next_unsolicited, reporting_snapshot, valid_sample


def sample(power=25, current=0.11, *, when=10, retained=False, **overrides):
    data = {"power": power, "current": current, "voltage": 241.2,
            "energy": 0.012, "state_relay": "ON"}
    data.update(overrides)
    return Sample(timestamp=when, retained=retained, data=data)


def test_charger_removed_must_publish_fresh_zero_without_get():
    events = [sample(25, .11, when=9), sample(25, .11, when=12)]
    assert next_unsolicited(events, 10, "unloaded") is None
    events.append(sample(0, 0, when=13))
    assert next_unsolicited(events, 10, "unloaded") == events[-1]


def test_explicit_read_or_cached_old_sample_cannot_satisfy_off_phase():
    events = [sample(0, 0, when=8), sample(0, 0, when=11, retained=True)]
    assert next_unsolicited(events, 10, "unloaded") is None


def test_loaded_event_requires_real_wattage_and_current():
    assert next_unsolicited([sample(25, .11, when=12)], 10, "loaded")
    assert next_unsolicited([sample(0, 0, when=12)], 10, "loaded") is None
    assert next_unsolicited([sample(25, 0, when=12)], 10, "loaded") is None

def test_retain_flag_cannot_count_as_new_measurement():
    assert next_unsolicited([sample(0, 0, when=12, retained=True)], 10, "unloaded") is None


def test_requires_standard_names_not_stale_legacy_switch_keys():
    old = sample(0, 0, when=11)
    old.data.pop("power")
    old.data["power_switch"] = 0
    ok, reason = valid_sample(old, "unloaded")
    assert not ok and "standard" in reason


def test_invalid_voltage_relay_energy_and_nan_fail_closed():
    for event in (sample(0, 0, voltage=0), sample(0, 0, state_relay="OFF"),
                  sample(0, 0, energy=-1), sample(float("nan"), 0)):
        assert not valid_sample(event, "unloaded")[0]


def test_stale_25_w_is_an_explicit_unload_failure():
    assert not valid_sample(sample(25, .11), "unloaded")[0]


def test_zero_load_tolerance_and_loaded_threshold():
    assert valid_sample(sample(0.5, .01), "unloaded")[0]
    assert not valid_sample(sample(2, .02), "unloaded")[0]
    assert valid_sample(sample(6, .04), "loaded")[0]
    assert not valid_sample(sample(4.9, .04), "loaded")[0]


def test_reporting_configuration_extraction_is_targeted():
    report = {"cluster": 2820, "attrId": 1291, "maxRepIntval": 65000}
    device = {"endpoints": {"1": {"configured_reportings": [report, {"cluster": 6, "attrId": 0}]}}}
    assert reporting_snapshot(device) == [report]
    assert report["maxRepIntval"] > 60  # Existing live 18-hour max is not a freshness guarantee.


def test_home_assistant_loaded_and_unloaded_thresholds():
    from live_bseed_pm_metering import ha_matches
    assert ha_matches("25.0", "loaded", 5, 1)
    assert ha_matches("0", "unloaded", 5, 1)
    assert not ha_matches("25.0", "unloaded", 5, 1)
    assert not ha_matches("unavailable", "unloaded", 5, 1)
    assert not ha_matches("nan", "unloaded", 5, 1)


def test_no_earlier_sample_may_satisfy_new_physical_phase():
    events = [sample(25, .11, when=2), sample(0, 0, when=4)]
    assert next_unsolicited(events, 5, "unloaded") is None
    events.append(sample(0, 0, when=6))
    assert next_unsolicited(events, 5, "unloaded") == events[-1]
