"""Release wiring checks for the BSEED TS0726 device_config guard."""

from pathlib import Path


def test_v5_release_build_enables_board_specific_config_guard() -> None:
    build = Path("make_scripts/build_bseed_ts0726_v5.sh").read_text(encoding="utf-8")
    assert build.count("DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG") == 2


def test_v6_release_build_has_clean_identity_and_guard() -> None:
    build = Path("make_scripts/build_bseed_ts0726_v6.sh").read_text(encoding="utf-8")
    assert "1.1.6-bseedv6" in build
    assert "0x11023007" in build
    assert "285356039" in build
    assert build.count("DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG") == 2
    assert "MIGRATION_REVERT" not in build


def test_v8_build_preserves_accepted_identity_and_guard() -> None:
    build = Path("make_scripts/build_bseed_ts0726_v8.sh").read_text(encoding="utf-8")
    assert "1.1.8-bseedv8" in build
    assert "0x1102300A" in build
    assert "285356042" in build
    assert "IMAGE_TYPE=45577" in build
    assert "MANUFACTURER_CODE=4417" in build

    # V8 deliberately centralizes the guard in COMMON_ARGS instead of
    # duplicating it in individual build/OTA invocations. All three artifacts
    # (BIN, normal OTA and stock-conversion OTA) must consume that same guarded
    # argument set.
    assert build.count("DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG") == 1
    common_start = build.index("COMMON_ARGS=(")
    common_end = build.index("\n)\n", common_start)
    common_args = build[common_start:common_end]
    assert "DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG" in common_args
    assert build.count('"${COMMON_ARGS[@]}"') == 3
    assert 'BIN_FILE="$BIN"' in build
    assert 'OTA_FILE="$OTA"' in build
    assert 'OTA_FILE="$FROM_TUYA_OTA"' in build

    assert "MIGRATION_REVERT" not in build
    assert "flash:" not in build
    assert "make -C src/telink flash" not in build


def test_telink_makefile_maps_guard_to_compile_time_define() -> None:
    makefile = Path("src/telink/Makefile").read_text(encoding="utf-8")
    assert "-DDEVICE_CONFIG_GUARD_$(DEVICE_CONFIG_GUARD)" in makefile


def test_firmware_guard_rechecks_identity_topology_and_gpio_uniqueness() -> None:
    source = Path("src/device_config/config_nv.c").read_text(encoding="utf-8")
    assert "#ifdef DEVICE_CONFIG_GUARD_BSEED_TS0726_3GANG" in source
    assert '"iedhxgyi"' in source
    assert '"TS0726-3-BS"' in source
    assert "network_count == 1" in source
    assert "switch_count == 3" in source
    assert "relay_count == 3" in source
    assert "indicator_count == 3" in source
    assert "momentary_count == 1" in source
    assert '"SLP"' in source
    assert "bseed_digits_only" in source
    assert "used_pins[pin_id]" in source
