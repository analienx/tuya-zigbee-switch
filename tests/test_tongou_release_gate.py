"""Prevent accidental publication of experimental, protection-critical firmware."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET = "DIN_RELAY_TONGOU_TOQSY2_163JZT"


def test_tongou_device_is_not_releasable_or_stock_ota_enabled():
    devices = yaml.safe_load((ROOT / "device_db.yaml").read_text(encoding="utf-8"))
    board = devices[TARGET]
    assert board["build"] is False
    assert board["status"] == "in_progress"
    assert board["stock_manufacturer_id"] is None
    assert board["stock_image_type"] is None
    assert board["firmware_image_type"] == 60013
    assert board["stock_model_name"] == "TS011F"
    assert board["stock_manufacturer_name"] == "_TZ3000_cayepv1a"


def test_tongou_is_not_present_in_existing_ota_indices():
    ota = ROOT / "zigbee2mqtt" / "ota"
    for path in ota.glob("index*.json"):
        contents = path.read_text(encoding="utf-8")
        assert "cayepv1a" not in contents, path
        assert "TS011F-TOQSY2" not in contents, path
