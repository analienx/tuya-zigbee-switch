"""Release-contract tests for the BSEED TS0726 v5 overlay."""

from __future__ import annotations

import shutil
import subprocess
import sys
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


def test_physical_policy_ux_is_clear_and_smart_light_oriented() -> None:
    js = source()
    assert 'channelLabel(feature.endpoint || expose.endpoint) + " — Logical state"' in js
    assert 'channelLabel(expose.endpoint) + " — State after power-up"' in js
    assert "not the same as mains power" in js
    assert 'lookup: {"Follow logical state": 0, "Always on": 1, "Always off": 2}' in js
    assert 'channelLabel(endpointName) + " — Mains power"' in js
    assert "smart bulbs and smart dimmers choose Always on" in js
    assert "affect power immediately" in js


def test_indicator_source_has_new_modes_without_reinterpreting_legacy_values() -> None:
    js = source()
    for mapping in (
        '"Logical state": 0',
        '"Inverse logical state": 1',
        '"Manual": 2',
        '"Physical output": 3',
        '"Binding status": 4',
    ):
        assert mapping in js
    assert 'channelLabel(endpointName) + " — LED shows"' in js
    assert "Binding status is recommended" in js
    assert "intent, not confirmation of remote state" in js
    assert "control only the panel LED" in js
    assert "lookup: {same:" not in js
    assert '"Rocker / toggle": 0' in js
    assert '"Push button": 1' in js
    assert '"Push button (normally closed)": 2' in js
    assert '"Toggle": 2' in js
    assert '"Match local state": 3' in js
    assert '"Never (detached)": 0' in js
    assert 'lookup: {Left: 1, Middle: 2, Right: 3}' in js


def test_binding_intent_state_is_exposed_per_channel_with_reconciliation_warning() -> None:
    js = source()
    for channel in ("left", "middle", "right"):
        assert (
            f'bindingIntentState("relay_{channel}_binding_intent", "relay_{channel}")'
            in js
        )
    assert 'channelLabel(endpointName) + " — Bound light (tracked)"' in js
    assert "not remote-state confirmation" in js
    assert "sends no command" in js
    assert "changes no binding" in js
    assert "attribute: {ID: 0xff04, type: 0x10}" in js


def test_advanced_editor_is_click_to_unlock_and_fail_closed() -> None:
    js = source()
    assert '.enum("device_config_unlock", ea.SET, ["enable_editing"])' in js
    assert '.withLabel("Advanced — Enable editing")' in js
    assert "DEVICE_CONFIG_UNLOCK_MS = 60_000" in js
    assert "deviceConfigUnlocks = new Map()" in js
    assert "requireDeviceConfigUnlock(meta)" in js
    assert "deviceConfigUnlocks.delete(unlockKey)" in js
    assert "Click 'Enable advanced editing'" in js
    assert '.withEndpoint("advanced")' in js
    assert '.withProperty("device_config_unlock")' in js
    assert "The button itself changes nothing" in js


def test_advanced_editor_validates_this_exact_board_before_transport() -> None:
    js = source()
    assert 'tokens[0] !== "iedhxgyi"' in js
    assert 'tokens[1] !== "TS0726-3-BS"' in js
    assert "network.length !== 1" in js
    assert "switches.length !== 3" in js
    assert "relays.length !== 3" in js
    assert "indicators.length !== 3" in js
    assert "momentary.length !== 1" in js
    assert "GPIO pin(s) assigned more than once" in js


def test_transport_extends_builtin_basic_without_shadow_cluster() -> None:
    js = source()
    assert 'deviceAddCustomCluster("genBasic"' in js
    assert 'name: "genBasic"' in js
    assert '"bseedBasicTransport"' not in js
    assert '"deviceConfigStage"' in js
    assert '"deviceConfigCommit"' in js


def test_device_config_is_editable_but_uses_chunked_transport_not_direct_write() -> None:
    js = source()
    assert 'deviceConfigEditable("device_config")' in js
    assert '.withLabel("Advanced — Hardware configuration")' in js
    assert '.text(name, ea.ALL)' in js
    assert '.withEndpoint("advanced")' in js
    assert '.withProperty(name)' in js
    assert "DEVICE_CONFIG_CHUNK_MAX = 24" in js
    assert '"deviceConfigStage"' in js
    assert '"deviceConfigCommit"' in js
    assert "crc16CcittFalse" in js
    assert "all chunks and CRC" in js
    assert "recovery firmware" in js
    assert "Editing is locked by default" in js
    # The custom SET path must not call endpoint.write().
    config_block = js[js.index("const deviceConfigEditable"):js.index("const legacyActionEvent")]
    assert ".write(" not in config_block
    assert '.read("genBasic", [0xff00]' in config_block


def test_channel_labels_are_self_identifying_when_frontend_hides_endpoint_headings() -> None:
    js = source()
    for endpoint, label in (
        ("switch_left", "Left"),
        ("switch_middle", "Middle"),
        ("switch_right", "Right"),
        ("relay_left", "Left"),
        ("relay_middle", "Middle"),
        ("relay_right", "Right"),
    ):
        assert f'{endpoint}: "{label}"' in js

    for suffix in (
        " — Mains power",
        " — Button type",
        " — Direct-binding command",
        " — Update local state",
        " — Control bound light",
        " — LED shows",
        " — Bound light (tracked)",
        " — Manual LED",
        " — Last button input",
    ):
        assert suffix in js


def test_advanced_config_is_a_dedicated_final_endpoint_group() -> None:
    js = source()
    assert "advanced: 1" in js
    assert '.withEndpoint("advanced")' in js
    assert 'deviceConfigEditable("device_config")' in js

    logical = js.index('logicalOnOff(["relay_left", "relay_middle", "relay_right"])')
    physical = js.index('physicalRelayMode("relay_left_physical_mode"')
    buttons = js.index('buttonType("switch_left_mode"')
    indicators = js.index('indicatorBehavior("relay_left_indicator_mode"')
    diagnostics = js.index('lastButtonAction("switch_left_press_action"')
    unlock = js.index("deviceConfigUnlock()")
    advanced = js.index('deviceConfigEditable("device_config")')
    assert logical < physical < buttons < indicators < diagnostics < unlock < advanced


def test_fail_closed_static_audit() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "helper_scripts/audit_bseed_ts0726_v5_overlay.py",
            str(OVERLAY),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"status": "PASS"' in result.stdout
    assert '"clickToUnlock": true' in result.stdout
    assert '"boardStructureValidation": true' in result.stdout
    assert '"channelLabelsSelfIdentify": true' in result.stdout
    assert '"advancedEndpointAlias": true' in result.stdout


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
    assert '"lockedSetRejectedWithoutTraffic": true' in transport.stdout
    assert '"unlockButtonEmitsNoZigbeeTraffic": true' in transport.stdout
    assert '"unlockConsumedAfterOneValidSave": true' in transport.stdout
    assert '"invalidBoardLayoutsRejectedWithoutTraffic": true' in transport.stdout
