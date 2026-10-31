"""Coordinator-to-Router diagnostics: directionality, adapter, privacy and no mutations."""
import importlib.util
from pathlib import Path

SCRIPT = Path("helper_scripts/bseed_coordinator_route_triage.py")
spec = importlib.util.spec_from_file_location("bseed_route_triage", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_reverse_path_failure_distinguished_from_inbound():
    lines = [
        "2026-10-11 NWK_NO_ROUTE at 0x69D8 for read",
        "2026-10-11 Received Zigbee message from 0xa4c138ac993cc245",
        "2026-10-11 device_announce (27096)",
        "2026-10-11 NWK_NO_ROUTE for other device 0x1234",
    ]
    out = module.diagnose(lines, ieee="0xa4c138ac993cc245", nwk=0x69D8,
                          adapter="zstack")
    assert out["classification"] == "POSSIBLE_ASYMMETRIC_COORDINATOR_ROUTE"
    assert out["counts"]["NWK_NO_ROUTE"] == 1
    assert out["counts"]["INBOUND_EVIDENCE"] == 2
    assert out["mutations_performed"] == 0
    assert "Z-Stack" in out["recommendations"][-1]


def test_no_false_positive_and_unknown_adapter_warns():
    out = module.diagnose(["NWK_NO_ROUTE for 0xAAAA",
                           "Received Zigbee message from 0xBBBB"],
                          ieee=None, nwk=0x69D8, adapter="unknown")
    assert out["classification"] == "INSUFFICIENT_TARGET_EVIDENCE"
    assert out["target_matched_lines"] == 0
    assert "Confirm adapter family" in out["recommendations"][-1]


def test_ember_and_nwk_decimal_are_separate():
    out = module.diagnose(["Route SOURCE_ROUTE_FAILURE 27096"],
                          ieee=None, nwk=27096, adapter="ember")
    assert out["counts"]["SOURCE_ROUTE_FAILURE"] == 1
    assert "Ember" in out["recommendations"][-1]


def test_validation_and_no_raw_logs_in_output():
    import pytest
    with pytest.raises(ValueError):
        module.diagnose(["anything"], ieee="bad", nwk=None, adapter="unknown")
    with pytest.raises(ValueError):
        module.diagnose(["anything"], ieee=None, nwk=65536, adapter="zstack")
    out = module.diagnose(["password secret NWK_NO_ROUTE for 0x69D8"],
                          ieee=None, nwk=0x69D8, adapter="zstack")
    assert "password" not in str(out)
    assert "secret" not in str(out)
