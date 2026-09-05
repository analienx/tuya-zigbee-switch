"""Regression coverage for the V8 transactional device_config preflight."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path


ROUTER = Path("build/stub/stub_device")
END_DEVICE = Path("build/stub/stub_end_device")
NVM_CONFIG = Path("stub_nvm_data/item_02.bin")


def _run(config: str, binary: Path = ROUTER) -> str:
    proc = subprocess.run(
        [str(binary), "--device-config", config, "--freeze-time"],
        input="q\n",
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    return output


def _stored_config() -> str:
    raw = NVM_CONFIG.read_bytes()
    assert len(raw) == 130
    size = struct.unpack_from("<H", raw, 0)[0]
    return raw[2 : 2 + size].decode("ascii")


def _assert_fallback_preserves_nvm(config: str, binary: Path = ROUTER) -> str:
    output = _run(config, binary)
    assert "Stored device config is unsafe; using compiled default in RAM" in output
    assert _stored_config() == config
    return output


def test_exact_current_endpoint_and_cluster_pool_boundary_is_accepted() -> None:
    # 4 switch endpoints + 4 relay endpoints + 2 cover endpoints = 10.
    # Router clusters: Basic+OTA 2 + 4*4 + 4*3 + 2*1 = exactly 32.
    config = (
        "StubManufacturer;StubDevice;LC0;"
        "SA0u;SA1u;SA2u;SA3u;"
        "RB0;RB1;RB2;RB3;"
        "CC0C1;CC2C3;"
    )
    output = _run(config)
    assert "Stored device config is unsafe" not in output
    assert "Initializing Zigbee with 4 switches, 4 relays, 0 cover switches, 2 covers" in output
    assert _stored_config() == config


def test_same_boundary_fails_closed_for_end_device_poll_cluster() -> None:
    # END_DEVICE adds PollControl to endpoint 1, turning the router's exact
    # 32-cluster boundary into 33. The stored candidate must remain untouched.
    config = (
        "StubManufacturer;StubDevice;LC0;"
        "SA0u;SA1u;SA2u;SA3u;"
        "RB0;RB1u;RB2;RB3;"
        "CC0C1;CC2C3;"
    )
    output = _assert_fallback_preserves_nvm(config, END_DEVICE)
    assert "Initializing Zigbee with 0 switches, 0 relays, 0 cover switches, 0 covers" in output


def test_switch_cluster_n_plus_one_rejects_whole_candidate() -> None:
    config = "Stub;Stub;SA0u;SA1u;SA2u;SA3u;SA4u;"
    output = _assert_fallback_preserves_nvm(config)
    assert "Initializing Zigbee with 0 switches, 0 relays, 0 cover switches, 0 covers" in output


def test_relay_cluster_n_plus_one_rejects_whole_candidate() -> None:
    config = "Stub;Stub;RA0;RA1;RA2;RA3;RA4;"
    _assert_fallback_preserves_nvm(config)


def test_led_storage_n_and_n_plus_one() -> None:
    safe = "Stub;Stub;LA0;LA1;LA2;LA3;LA4;"
    output = _run(safe)
    assert "Stored device config is unsafe" not in output

    unsafe = "Stub;Stub;LA0;LA1;LA2;LA3;LA4;LA5;"
    _assert_fallback_preserves_nvm(unsafe)


def test_button_storage_n_and_n_plus_one() -> None:
    pins = [f"A{i}" for i in range(8)] + ["B0", "B1", "B2", "B3"]
    safe = "Stub;Stub;" + "".join(f"B{pin}u;" for pin in pins[:11])
    output = _run(safe)
    assert "Stored device config is unsafe" not in output

    unsafe = "Stub;Stub;" + "".join(f"B{pin}u;" for pin in pins[:12])
    _assert_fallback_preserves_nvm(unsafe)


def test_malformed_known_token_is_rejected_before_hardware_parse() -> None:
    _assert_fallback_preserves_nvm("Stub;Stub;Dnot-a-number;SA0u;")
    _assert_fallback_preserves_nvm("Stub;Stub;SA0;")
    _assert_fallback_preserves_nvm("Stub;Stub;RA0A1X;")


def test_source_contract_preflights_only_after_device_migration() -> None:
    source = Path("src/app.c").read_text(encoding="utf-8")
    migration = source.index("handle_device_specific_migrations()")
    enable = source.index("device_config_enable_parser_preflight()")
    parse = source.index("parse_config();")
    assert migration < enable < parse


def test_runtime_capacity_constants_match_concrete_parser_arrays() -> None:
    header = Path("src/device_config/config_nv.h").read_text(encoding="utf-8")
    parser = Path("src/device_config/config_parser.c").read_text(encoding="utf-8")

    expected = {
        "DEVICE_CONFIG_MAX_LEDS": ("led_t   leds[5]", "5"),
        "DEVICE_CONFIG_MAX_BUTTONS": ("button_t buttons[11]", "11"),
        "DEVICE_CONFIG_MAX_RELAYS": ("relay_t relays[10]", "10"),
        "DEVICE_CONFIG_MAX_SWITCH_CLUSTERS": (
            "zigbee_switch_cluster switch_clusters[4]",
            "4",
        ),
        "DEVICE_CONFIG_MAX_RELAY_CLUSTERS": (
            "zigbee_relay_cluster relay_clusters[4]",
            "4",
        ),
        "DEVICE_CONFIG_MAX_ENDPOINTS": (
            "hal_zigbee_endpoint endpoints[10]",
            "10",
        ),
        "DEVICE_CONFIG_CLUSTER_POOL_SIZE": (
            "hal_zigbee_cluster  clusters[32]",
            "32",
        ),
    }
    for macro, (declaration, value) in expected.items():
        assert declaration in parser
        assert f"#define {macro}" in header
        line = next(line for line in header.splitlines() if line.startswith(f"#define {macro}"))
        assert line.split()[-1] == value
