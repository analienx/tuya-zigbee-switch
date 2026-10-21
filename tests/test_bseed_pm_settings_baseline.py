"""Offline tests for the legacy-safe PM settings baseline helper."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))

from bseed_pm_settings_baseline import capture  # noqa: E402

IEEE = "0xa4c138075cd16ed4"


def record(**changes):
    data = {
        "ieeeAddr": IEEE,
        "type": "Router",
        "manufName": "b28wrpvx",
        "modelID": "TS011F-BS-PM",
        "swBuildId": None,
        "nwkAddr": 13395,
        "endpoints": {
            "1": {
                "clusters": {
                    "genOnOffSwitchCfg": {
                        "attributes": {"startUpOnOff": 0, "switchType": 0}
                    },
                    "manuSpecificTuya3": {
                        "attributes": {"relayStatus": 0}
                    },
                },
                "configuredReportings": [],
                "binds": [],
            },
            "2": {"clusters": {"genOnOff": {"attributes": {"onOff": 0}}}},
        },
    }
    data.update(changes)
    return data


def test_legacy_router_baseline_needs_no_basic_build():
    evidence = capture(record(), ieee=IEEE, expect_role="Router")
    assert evidence["result"] == "read_only_settings_baseline"
    assert evidence["writes"] == 0
    assert evidence["source_swBuildId"] is None
    assert evidence["settings_snapshot"]["1/genOnOffSwitchCfg"]["startUpOnOff"] == 0
    assert "onOff" not in evidence["settings_snapshot"].get("2/genOnOff", {})


@pytest.mark.parametrize(
    "changes,role,match",
    [
        ({"ieeeAddr": "0x0000000000000001"}, "Router", "IEEE/role"),
        ({"type": "EndDevice"}, "Router", "IEEE/role"),
        ({"manufName": "o1jzcxou"}, "Router", "exact BSEED PM board"),
        ({"modelID": "TS011F-BS"}, "Router", "exact BSEED PM board"),
    ],
)
def test_baseline_rejects_wrong_identity(changes, role, match):
    with pytest.raises(ValueError, match=match):
        capture(record(**changes), ieee=IEEE, expect_role=role)


def test_baseline_rejects_empty_settings():
    rec = record()
    rec["endpoints"] = {"1": {"clusters": {}, "configuredReportings": [], "binds": []}}
    with pytest.raises(ValueError, match="No known PM socket settings"):
        capture(rec, ieee=IEEE, expect_role="Router")
