"""Offline legacy-source proof tests; no MQTT, HA or firmware writes."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))

from bseed_live_source_identity import (  # noqa: E402
    legacy_pm_router_file_version,
    require_check_record_source,
    require_fresh_source,
    require_inventory_source,
)

IEEE = "0xa4c138075cd16ed4"
RC1 = 302329869


def profile(**changes):
    data = {
        "device": "KitchenSocketRight",
        "ieee": IEEE,
        "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM",
        "preflash_role": "Router",
        "preflash_build": "1.2.5-bseedv8u5-rc1",
        "preflash_file_version": RC1,
        "require_pm": True,
        "non_pm": False,
    }
    data.update(changes)
    return data


def target(*, build=None, version=RC1, role="Router", ieee=IEEE):
    device = {"ieeeAddr": ieee, "type": role}
    if build is not None:
        device["softwareBuildID"] = build
    return {
        "state_relay": "OFF",
        "device": device,
        "update": {"installed_version": version, "state": "idle"},
    }


def test_exact_legacy_pm_router_uses_fresh_ota_version_when_basic_missing():
    cfg = profile()
    inv = {"software_build_id": None}
    assert require_inventory_source(cfg, inv)["method"].startswith("legacy_pm_router")
    proof = require_fresh_source(cfg, target())
    assert proof == {
        "method": "ota_file_version",
        "build": cfg["preflash_build"],
        "file_version": RC1,
    }


def test_present_wrong_basic_never_falls_back_to_matching_file_version():
    cfg = profile()
    with pytest.raises(ValueError, match="Basic build mismatched"):
        require_fresh_source(
            cfg, target(build="1.2.5-bseedr13", version=RC1)
        )


def test_wrong_or_missing_legacy_file_version_is_rejected():
    cfg = profile()
    with pytest.raises(ValueError, match="fileVersion"):
        require_fresh_source(cfg, target(version=RC1 + 1))
    without = target()
    without.pop("update")
    with pytest.raises(ValueError, match="fileVersion"):
        require_fresh_source(cfg, without)


@pytest.mark.parametrize(
    "changes",
    [
        {"preflash_role": "EndDevice"},
        {"manufacturer": "o1jzcxou", "model": "TS011F-BS", "require_pm": False, "non_pm": True},
        {"model": "TS0726-3-BS"},
        {"manufacturer": "_TZ3000_b28wrpvx"},
    ],
)
def test_numeric_fallback_is_pm_router_only(changes):
    with pytest.raises(ValueError, match="exact custom PM Router"):
        legacy_pm_router_file_version(profile(**changes))


def test_flash_check_record_must_carry_exact_legacy_source_proof():
    cfg = profile()
    good = {
        "source_identity": {
            "method": "ota_file_version",
            "build": cfg["preflash_build"],
            "file_version": RC1,
        }
    }
    assert require_check_record_source(cfg, good) is True
    for bad in (
        {},
        {"source_identity": {"method": "basic_build", "build": cfg["preflash_build"], "file_version": None}},
        {"source_identity": {"method": "ota_file_version", "build": cfg["preflash_build"], "file_version": RC1 + 1}},
        {"source_identity": {"method": "ota_file_version", "build": "wrong", "file_version": RC1}},
    ):
        with pytest.raises(AssertionError, match="LAST_CHECK"):
            require_check_record_source(cfg, bad)


def test_normal_basic_source_does_not_need_numeric_fallback():
    cfg = profile(preflash_file_version=None)
    inv = {"software_build_id": cfg["preflash_build"]}
    assert require_inventory_source(cfg, inv)["method"] == "basic_build"
    proof = require_fresh_source(cfg, target(build=cfg["preflash_build"], version=None))
    assert proof["method"] == "basic_build"
    assert require_check_record_source(cfg, {}) is True
