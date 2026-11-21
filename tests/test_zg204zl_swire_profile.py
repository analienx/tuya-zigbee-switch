"""Fail-closed ZG-204ZL/TLSR8258 inspection-profile contract.

No serial port, target, OTA network, or flash is accessed by these tests.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import telink_swire_recovery as swire  # noqa: E402


def test_zg204zl_is_a_distinct_tlsr8253_profile():
    profile = swire.DEVICES["zg-204zl"]
    assert profile["silicon"] == "TLSR8258"
    assert profile["inspectionOnly"] is True
    assert profile["stockFirmwareIdentityDocumented"] == {
        "manufacturerCode": 0x1286, "imageType": 0x0203
    }
    assert profile["customFirmwareIdentityDocumented"] == {
        "manufacturerCode": 0x1141, "imageType": 0x0232
    }
    assert "zg-204zl" != "generic-tlsr8258"


def test_zg204zl_wiring_exposes_exact_pvvx_connector_and_stop_warning():
    text = swire.wiring("zg-204zl", "cp2102-single-sws")
    assert "pin 3 = TLSR8258 PA7/SWS" in text
    assert "pin 2 = GND" in text
    assert "pin 1 = battery INPUT" in text
    assert "EXTERNAL SUPPLY -------------- +Vbat" in text
    assert "GPIO supply" in text
    assert "1.2 kOhm alone is NOT a level shifter" in text
    assert "Before you connect:" in text
    assert "The 3.3 V rail reads 3.2-3.4 V" not in text
    assert "TXD --[1.2 kOhm]--+-- one lead -- SWS" in text
    assert "inspection-only" in text
    assert "Do not use prepare, flash, stage" in text
    assert "regulated 2.1 V" in text


@pytest.mark.parametrize("kind", ["ota", "image", "default"])
def test_zg204zl_never_creates_tlsr8258_write_bundle(tmp_path, kind):
    out = tmp_path / "forbidden_bundle"
    input_file = tmp_path / "unexpected_payload.bin"
    # Even an absent/invalid image must fail *before* file opening or network I/O.
    kwargs = ({"ota_path": input_file} if kind == "ota"
              else {"image_path": input_file} if kind == "image" else {})
    with pytest.raises(swire.RecoveryError) as failed:
        swire.prepare(out=out, device="zg-204zl", **kwargs)
    assert failed.value.code == "UNQUALIFIED_FLASH_LAYOUT"
    assert failed.value.retry_safe is False
    assert not out.exists()


def test_zg204zl_direct_device_checker_also_fails_closed():
    with pytest.raises(swire.RecoveryError) as failed:
        swire.check_device("zg-204zl", {
            "manufacturerCode": 0x1286, "imageType": 0x0203
        }, {})
    assert failed.value.code == "UNQUALIFIED_FLASH_LAYOUT"


def test_zg204zl_target_link_fails_before_port_open(tmp_path):
    receipt = tmp_path / "test-link.json"
    with pytest.raises(swire.RecoveryError) as failed:
        swire.test_link("COM3", receipt, phase="target", device="zg-204zl")
    assert failed.value.code == "SWIRE_VOLTAGE_UNQUALIFIED"
    assert not receipt.exists()


def test_zg204zl_watch_fails_before_port_or_directory_creation(tmp_path):
    output = tmp_path / "watch"
    with pytest.raises(swire.RecoveryError) as failed:
        swire.watch_link("COM3", output, device="zg-204zl")
    assert failed.value.code == "SWIRE_VOLTAGE_UNQUALIFIED"
    assert not output.exists()
