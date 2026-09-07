"""v5 indicator source and binding-intent behavioral tests."""

from pathlib import Path

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_ONOFF_BINDING_INTENT_STATE,
    ZCL_ATTR_ONOFF_INDICATOR_MODE,
    ZCL_ATTR_ONOFF_INDICATOR_STATE,
    ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT,
    ZCL_ONOFF_INDICATOR_MODE_MANUAL,
    ZCL_ONOFF_INDICATOR_MODE_OPPOSITE,
    ZCL_ONOFF_INDICATOR_MODE_PHYSICAL_OUTPUT,
    ZCL_ONOFF_INDICATOR_MODE_SAME,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON,
)

DEVICE_CONFIG = "TestManufacturer;TestDev;SA0u;RB0;IA1;M;"
SWITCH_EP = 1
RELAY_EP = 2
BUTTON_PIN = "A0"
RELAY_PIN = "B0"
LED_PIN = "A1"


def read_int(device: Device, attr: int) -> int:
    return int(device.read_zigbee_attr(RELAY_EP, ZCL_CLUSTER_ON_OFF, attr))


def write(device: Device, attr: int, value: int) -> None:
    device.write_zigbee_attr(RELAY_EP, ZCL_CLUSTER_ON_OFF, attr, value)


def test_physical_output_source_tracks_effective_mains_not_logical_state() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_PHYSICAL_OUTPUT)

        write(d, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
        d.zcl_relay_off(RELAY_EP)
        assert d.get_gpio(RELAY_PIN, refresh=True)
        assert d.get_gpio(LED_PIN, refresh=True)
        d.zcl_relay_on(RELAY_EP)
        assert d.get_gpio(LED_PIN, refresh=True)

        write(d, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF)
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        assert not d.get_gpio(LED_PIN, refresh=True)
        d.zcl_relay_on(RELAY_EP)
        assert not d.get_gpio(LED_PIN, refresh=True)

        write(d, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED)
        d.zcl_relay_on(RELAY_EP)
        assert d.get_gpio(RELAY_PIN, refresh=True)
        assert d.get_gpio(LED_PIN, refresh=True)
        d.zcl_relay_off(RELAY_EP)
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        assert not d.get_gpio(LED_PIN, refresh=True)


def test_binding_intent_toggle_requires_real_binding_and_never_cuts_mains() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        write(d, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT)
        d.clear_bindings()

        initial = read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE)
        d.click_button(BUTTON_PIN)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == initial
        assert d.get_gpio(RELAY_PIN, refresh=True)

        d.add_binding(SWITCH_EP, ZCL_CLUSTER_ON_OFF)
        d.clear_events()
        d.click_button(BUTTON_PIN)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == (0 if initial else 1)
        assert d.get_gpio(LED_PIN, refresh=True) == (not bool(initial))
        assert len(d.zcl_list_cmds(endpoint=SWITCH_EP, cluster=ZCL_CLUSTER_ON_OFF)) == 1
        assert d.get_gpio(RELAY_PIN, refresh=True)

        d.clear_events()
        d.click_button(BUTTON_PIN)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == initial
        assert d.get_gpio(LED_PIN, refresh=True) == bool(initial)
        assert len(d.zcl_list_cmds(endpoint=SWITCH_EP, cluster=ZCL_CLUSTER_ON_OFF)) == 1
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_external_binding_intent_reconciliation_emits_no_bound_command() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.add_binding(SWITCH_EP, ZCL_CLUSTER_ON_OFF)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT)
        d.clear_events()

        write(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE, 1)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == 1
        assert d.get_gpio(LED_PIN, refresh=True)
        assert d.zcl_list_cmds() == []

        write(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE, 0)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == 0
        assert not d.get_gpio(LED_PIN, refresh=True)
        assert d.zcl_list_cmds() == []


def test_level_move_marks_binding_intent_on_stop_does_not_toggle() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.clear_bindings()
        d.add_binding(SWITCH_EP, ZCL_CLUSTER_LEVEL_CONTROL)
        write(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE, 0)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT)

        d.long_click_button(BUTTON_PIN, 1000)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == 1
        assert d.get_gpio(LED_PIN, refresh=True)
        level_commands = d.zcl_list_cmds(
            endpoint=SWITCH_EP, cluster=ZCL_CLUSTER_LEVEL_CONTROL
        )
        assert len(level_commands) == 2  # MoveWithOnOff, then StopWithOnOff
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == 1


def test_binding_intent_and_source_persist_across_reboot() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        write(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE, 1)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert read_int(d, ZCL_ATTR_ONOFF_BINDING_INTENT_STATE) == 1
        assert read_int(d, ZCL_ATTR_ONOFF_INDICATOR_MODE) == ZCL_ONOFF_INDICATOR_MODE_BINDING_INTENT
        assert d.get_gpio(LED_PIN, refresh=True)


def test_legacy_indicator_enum_values_keep_existing_semantics() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)

        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_SAME)
        d.zcl_relay_on(RELAY_EP)
        assert d.get_gpio(LED_PIN, refresh=True)
        d.zcl_relay_off(RELAY_EP)
        assert not d.get_gpio(LED_PIN, refresh=True)

        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_OPPOSITE)
        d.zcl_relay_off(RELAY_EP)
        assert d.get_gpio(LED_PIN, refresh=True)
        d.zcl_relay_on(RELAY_EP)
        assert not d.get_gpio(LED_PIN, refresh=True)

        write(d, ZCL_ATTR_ONOFF_INDICATOR_MODE, ZCL_ONOFF_INDICATOR_MODE_MANUAL)
        write(d, ZCL_ATTR_ONOFF_INDICATOR_STATE, 1)
        d.zcl_relay_off(RELAY_EP)
        assert d.get_gpio(LED_PIN, refresh=True)
