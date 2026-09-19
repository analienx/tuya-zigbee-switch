"""Offline checks for the experimental slim TS0505B build recipe."""
from pathlib import Path

import pytest

from helper_scripts.ts0505b.build_slim import (
    APP_VERSION, CHIPS, MAX_RESEARCH_OTA_SIZE, SEED, inspect_local,
    pack_ota,
)
from helper_scripts.ts0505b.parse_zigbee_ota import parse_ota_header


def test_seed_is_self_contained_and_minimal():
    project = (SEED / "ts0505b_slim_reference.slcp").read_text()
    assert "project_name: ts0505b_slim_reference" in project
    assert "EFR32MG21A020F1024IM32" in project
    for omitted in ("zigbee_gp", "zigbee_zll", "zigbee_zcl_cli", "zigbee_core_cli",
                    "zigbee_find_and_bind_target", "zigbee_stack_diagnostics"):
        assert f"  id: {omitted}\n" not in project
    for source in ("app.c", "main.c"):
        assert (SEED / source).is_file()
    for source in ("ts0505b_light_state.c", "ts0505b_light_state.h",
                   "ts0505b_zcl_adapter.c", "ts0505b_zcl_adapter.h"):
        assert (SEED.parent / source).is_file()


def test_ota_wrapper_size_and_identity():
    wrapped = pack_ota(b"\x00" * 183172)
    header = parse_ota_header(wrapped)
    assert len(wrapped) == 183234
    assert header["declared_size_matches_file"] is True
    assert (header["manufacturer_code"], header["image_type"],
            header["file_version"]) == (0x100B, 0x020C, APP_VERSION)
    assert wrapped[56:62] == b"\x00\x00\x84\xcb\x02\x00"


def test_rejects_oversize_before_writing():
    with pytest.raises(ValueError, match="exceeds observed pre-byte acceptance"):
        pack_ota(b"\x00" * (MAX_RESEARCH_OTA_SIZE - 61))


def test_supported_variant_template_identity():
    assert set(CHIPS) == {768, 1024}
    assert CHIPS[768].endswith("F768IM32")
    assert CHIPS[1024].endswith("F1024IM32")


def test_parser_rejects_placeholder_gbl():
    with pytest.raises(ValueError):
        inspect_local(pack_ota(b"\x00" * 1024))
