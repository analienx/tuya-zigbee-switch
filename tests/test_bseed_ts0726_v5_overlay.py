"""Release-contract tests for the BSEED TS0726 v5 overlay."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

OVERLAY = Path("zigbee2mqtt/converters/bseed_ts0726_v5.js")


def source() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def test_exact_v5_fingerprint_only() -> None:
    js = source()
    assert 'manufacturerName: "iedhxgyi"' in js
    assert 'modelID: "TS0726-3-BS"' in js
    assert 'softwareBuildID: "1.1.5-bseedv5"' in js
    assert "priority: 100" in js
    assert "zigbeeModel:" not in js
    assert 'model: "EC-GL86ZPCS31"' in js


def test_configure_surface_remains_non_mutating() -> None:
    js = source()
    assert "configureReporting: false" in js
    assert "reporting.bind" not in js
    assert "endpoint.bind(" not in js
    assert "configure: async () => {}" in js


def test_physical_policy_ux_is_preserved() -> None:
    js = source()
    assert '"Logical relay state"' in js
    assert "does not necessarily switch mains power" in js
    assert '"Logical state after power-up"' in js
    assert 'lookup: {follow_state: 0, always_on: 1, always_off: 2}' in js
    assert '"Physical relay behavior"' in js
    assert "recommended for smart bulbs/dimmers" in js
    assert "immediately switch mains power" in js


def test_indicator_source_has_new_modes_without_reinterpreting_legacy_values() -> None:
    js = source()
    for mapping in (
        "logical_state: 0",
        "inverse_logical_state: 1",
        "manual: 2",
        "physical_output: 3",
        "binding_status: 4",
    ):
        assert mapping in js
    assert '"Indicator LED source"' in js
    assert "effective mains command" in js
    assert "local intent, not confirmation" in js
    assert "control only the panel LED, never the mains relay" in js
    # 'same' must not remain as the primary user-facing enum key.
    assert "lookup: {same:" not in js


def test_binding_intent_state_is_exposed_per_channel_with_reconciliation_warning() -> None:
    js = source()
    for channel in ("left", "middle", "right"):
        assert (
            f'bindingIntentState("relay_{channel}_binding_intent", "relay_{channel}")'
            in js
        )
    assert '"Bound-light intent state"' in js
    assert "not proof" in js
    assert "remote light's actual state" in js
    assert "does not send a bound-device" in js
    assert "command and does not change logical relay state or mains power" in js
    assert "does not change logical relay state or mains power" in js
    assert "attribute: {ID: 0xff04, type: 0x10}" in js


def test_device_config_is_editable_but_uses_chunked_transport_not_direct_write() -> None:
    js = source()
    assert 'deviceConfigEditable("device_config", "switch_left")' in js
    assert '"Advanced hardware configuration"' in js
    assert 'access: "ALL"' in js
    assert "DEVICE_CONFIG_CHUNK_MAX = 24" in js
    assert '"deviceConfigStage"' in js
    assert '"deviceConfigCommit"' in js
    assert "crc16CcittFalse" in js
    assert "complete coverage" in js
    assert "recovery firmware" in js
    # The custom SET path must not call endpoint.write().
    config_block = js[js.index("const deviceConfigEditable"):js.index("const legacyActionEvent")]
    assert ".write(" not in config_block
    assert '.read("genBasic", [0xff00]' in config_block


def test_advanced_config_remains_last_after_normal_controls() -> None:
    js = source()
    logical = js.index('logicalOnOff(["relay_left", "relay_middle", "relay_right"])')
    physical = js.index('physicalRelayMode("relay_left_physical_mode"')
    buttons = js.index('buttonType("switch_left_mode"')
    indicators = js.index('indicatorBehavior("relay_left_indicator_mode"')
    diagnostics = js.index('lastButtonAction("switch_left_press_action"')
    advanced = js.index('deviceConfigEditable("device_config"')
    assert logical < physical < buttons < indicators < diagnostics < advanced


def test_historical_action_api_is_preserved() -> None:
    js = source()
    assert "legacyActionEvent()" in js
    assert 'action: prefix + "_" + suffix' in js
    assert '["action_" + name]: suffix' in js
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


def test_javascript_syntax_and_behavioral_probes_when_node_is_available() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not available")

    subprocess.run(["node", "--check", str(OVERLAY)], check=True)

    action = subprocess.run(
        [
            "node",
            "helper_scripts/probe_bseed_ts0726_action_contract.js",
            str(OVERLAY),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"status": "PASS"' in action.stdout
    assert '"action": "switch_0_press"' in action.stdout

    transport = subprocess.run(
        [
            "node",
            "helper_scripts/probe_bseed_ts0726_v5_config_transport.js",
            str(OVERLAY),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"status": "PASS"' in transport.stdout
    assert '"exactRoundTrip": true' in transport.stdout
    assert '"directWriteCount": 0' in transport.stdout
