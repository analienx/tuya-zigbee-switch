import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "helper_scripts" / "make_z2m_custom_converters.py"


def _render(*extra: str) -> str:
    return subprocess.check_output(
        [sys.executable, str(GEN), *extra, "device_db.yaml"],
        cwd=ROOT,
        text=True,
    )


def _definition(text: str, zigbee_model: str) -> str:
    marker = f'"{zigbee_model}"'
    marker_pos = text.index(marker)
    start = text.rfind("\n    {\n", 0, marker_pos)
    assert start >= 0
    start += 1
    end = text.find("\n    {\n", marker_pos + len(marker))
    return text[start:] if end == -1 else text[start:end]


SOCKET_ONLY_SWITCH_CONTROLS = (
    "switch_press_action",
    "switch_mode",
    "switch_action_mode",
    "switch_relay_mode",
    "switch_relay_index",
    "switch_binded_mode",
    "switch_long_press_duration",
    "switch_level_move_rate",
)


def test_bseed_pm_outlet_hides_switch_and_dimmer_controls():
    for args in [(), ("--z2m-v1",)]:
        definition = _definition(_render(*args), "TS011F-BS-PM")
        for expose in SOCKET_ONLY_SWITCH_CONTROLS:
            assert expose not in definition
        assert "bseedSocketRelayOnOff()" in definition
        assert 'electricityMeter({' in definition
        assert 'commandsOnOff({' not in definition
        assert 'commandsLevelCtrl({' not in definition
        assert 'relay_physical_mode' in definition
        assert 'relay_indicator_mode' in definition


def test_non_pm_bseed_outlet_uses_same_socket_profile():
    for args in [(), ("--z2m-v1",)]:
        definition = _definition(_render(*args), "TS011F-BS")
        for expose in SOCKET_ONLY_SWITCH_CONTROLS:
            assert expose not in definition
        assert "bseedSocketRelayOnOff()" in definition
        assert 'electricityMeter()' not in definition
        assert 'commandsOnOff({' not in definition
        assert 'commandsLevelCtrl({' not in definition


def test_custom_firmware_matchers_preserve_safe_bseed_identity_rules():
    for args in [(), ("--z2m-v1",)]:
        rendered = _render(*args)
        assert '"TS011F-BS-PM"' in rendered
        assert '"TS011F-BS"' in rendered
        assert (
            '{ manufacturerName: "iedhxgyi", modelID: "TS0726-3-BS" }'
            in rendered
        )
        ts0726 = _definition(rendered, "TS0726-3-BS")
        assert 'zigbeeModel:' not in ts0726


def test_bseed_ts0726_dimmer_keeps_full_switch_controls():
    for args in [(), ("--z2m-v1",)]:
        definition = _definition(_render(*args), "TS0726-3-BS")
        assert "switch_left_mode" in definition
        assert "switch_left_action_mode" in definition
        assert "switch_left_relay_mode" in definition
        assert "switch_left_relay_index" in definition
        assert "switch_left_binded_mode" in definition
        assert "switch_left_long_press_duration" in definition
        assert "switch_left_level_move_rate" in definition
        assert "commandsOnOff({" in definition
        assert "commandsLevelCtrl({" in definition


def test_bseed_pm_meter_uses_standard_mqtt_properties_on_endpoint_one():
    skip = 'multiEndpointSkip: ["power", "current", "voltage", "energy"]'
    for args in [(), ("--z2m-v1",)]:
        rendered = _render(*args)
        for model in ("TS011F-BS-PM",):
            definition = _definition(rendered, model)
            assert skip in definition, model
            assert 'electricityMeter({' in definition
            assert 'power: {max: 60, multiplier: 1, divisor: 1}' in definition
            assert 'current: {max: 300, multiplier: 1, divisor: 1000}' in definition
            assert 'voltage: {max: 300, multiplier: 1, divisor: 100}' in definition
            assert 'energy: {max: 600, change: 1, multiplier: 1, divisor: 1000}' in definition
            assert '"switch": 1, "relay": 2' in definition
            assert 'meta: { multiEndpoint: true }' in definition
        assert skip not in _definition(rendered, "TS011F-BS-PM-1")
        assert skip not in _definition(rendered, "TS011F-BS-PM-2")
        assert skip not in _definition(rendered, "TS011F-BS")
        assert skip not in _definition(rendered, "TS0726-3-BS")


def test_bseed_pm_fresh_configure_has_scaling_for_all_four_reportings():
    """Forced native scales prevent setup reads from aborting bind/reporting."""
    definition = _definition(_render(), "TS011F-BS-PM")
    expected = {
        "power": (1, 1),
        "current": (1, 1000),
        "voltage": (1, 100),
        "energy": (1, 1000),
    }
    for name, (multiplier, divisor) in expected.items():
        line = next(line for line in definition.splitlines() if f"{name}: {{max:" in line)
        assert f"multiplier: {multiplier}, divisor: {divisor}" in line
