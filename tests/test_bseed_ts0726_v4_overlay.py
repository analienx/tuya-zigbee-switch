"""Release-contract tests for the standalone BSEED TS0726 v4 overlay."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

OVERLAY = Path("zigbee2mqtt/converters/bseed_ts0726_v4.js")


def source() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def test_overlay_is_exact_forward_firmware_fingerprint_only() -> None:
    js = source()

    assert 'manufacturerName: "iedhxgyi"' in js
    assert 'modelID: "TS0726-3-BS"' in js
    assert 'softwareBuildID: "1.1.4-bseedv4"' in js
    assert "priority: 100" in js
    assert "zigbeeModel:" not in js
    assert js.count('model: "EC-GL86ZPCS31"') == 1


def test_overlay_never_configures_bindings_or_reporting() -> None:
    js = source()

    assert "configureReporting: false" in js
    assert "reporting.bind" not in js
    assert "reporting.onOff" not in js
    assert "configureReporting(" not in js
    assert "endpoint.bind(" not in js
    assert "configure: async () => {}" in js


def test_logical_vs_physical_power_contract_is_explicit() -> None:
    js = source()

    assert '"Logical relay state"' in js
    assert "does not necessarily switch mains power" in js
    assert '"Logical state after power-up"' in js
    assert "Physical relay behavior is" in js

    assert 'lookup: {follow_state: 0, always_on: 1, always_off: 2}' in js
    assert '"Physical relay behavior"' in js
    assert "recommended for smart bulbs/dimmers" in js
    assert "immediately switch mains power" in js
    assert "persists across restart" in js


def test_new_physical_mode_properties_are_clean_and_channel_specific() -> None:
    js = source()

    for channel in ("left", "middle", "right"):
        prop = f"relay_{channel}_physical_mode"
        assert f'physicalRelayMode("{prop}", "relay_{channel}")' in js
    assert "expose.withProperty?.(name)" in js


def test_button_and_indicator_ux_is_human_readable() -> None:
    js = source()

    for label in (
        "Button type",
        "Button command behavior",
        "Local relay trigger",
        "Assigned local relay",
        "Bound-device trigger",
        "Long-press threshold",
        "Hold dimming speed",
        "Indicator LED behavior",
        "Indicator LED state",
        "Last button action",
        "Network indicator",
        "Factory-reset press count",
    ):
        assert f'"{label}"' in js

    assert "relay_1 = Left, relay_2 = Middle, relay_3 = Right" in js
    assert 'unit: "ms"' in js
    assert 'unit: "level/s"' in js
    assert "controls only the panel LED, never the mains relay" in js


def test_advanced_pin_map_is_read_only_and_last() -> None:
    js = source()

    assert '"Advanced hardware configuration (read-only)"' in js
    assert 'access: "STATE_GET"' in js
    assert "Firmware migration owns this" in js
    assert "may require recovery firmware" in js

    logical = js.index('logicalOnOff(["relay_left", "relay_middle", "relay_right"])')
    physical = js.index('physicalRelayMode("relay_left_physical_mode"')
    buttons = js.index('buttonType("switch_left_mode"')
    indicators = js.index('indicatorBehavior("relay_left_indicator_mode"')
    diagnostics = js.index('lastButtonAction("switch_left_press_action"')
    advanced = js.index('deviceConfigReadOnly("device_config"')

    assert logical < physical < buttons < indicators < diagnostics < advanced


def test_historical_action_api_is_preserved() -> None:
    js = source()

    assert "legacyActionEvent()" in js
    assert 'action: prefix + "_" + suffix' in js
    assert '["action_" + name]: suffix' in js

    for value in (
        "switch_0_press",
        "switch_1_toggle",
        "switch_2_brightness_move_up",
    ):
        # Values are constructed rather than hard-coded; prove their source
        # prefixes/suffixes are present in the compatibility decoder.
        prefix, suffix = value.split("_", 2)[0:2], None
    for prefix in ("switch_0", "switch_1", "switch_2"):
        assert f'prefix: "{prefix}"' in js
    for suffix in (
        '"press"',
        '"toggle"',
        '"brightness_move_up"',
        '"brightness_move_down"',
        '"brightness_stop"',
    ):
        assert suffix in js


def test_overlay_javascript_syntax_when_node_is_available() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not available in this test environment")

    subprocess.run(["node", "--check", str(OVERLAY)], check=True)
