"""Behavioral tests for BSEED v7 pure-local-relay mode.

These tests deliberately keep real OnOff and Level bindings installed.  The
safety contract is firmware-side: binded_mode=0 must suppress every outbound
bound command while local relay actuation remains functional.
"""

from tests.conftest import Device, StubProc
from tests.zcl_consts import (
    ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_BINDING_MODE,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
    ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
    ZCL_CMD_LEVEL_STOP_WITH_ON_OFF,
    ZCL_CMD_ONOFF_TOGGLE,
)

DISABLED = 0
SHORT_PRESS = 3


def _configured_device() -> tuple[StubProc, Device]:
    # One momentary switch controlling one relay.  The distinct pins avoid
    # simulator ambiguity and mirror the BSEED logical shape at minimum size.
    proc = StubProc(device_config="A;B;SA0u;RB0;M;").start()
    device = Device(proc)
    device.set_network(1)  # HAL_ZIGBEE_NETWORK_JOINED
    device.clear_bindings()
    device.add_binding(1, ZCL_CLUSTER_ON_OFF)
    device.add_binding(1, ZCL_CLUSTER_LEVEL_CONTROL)
    return proc, device


def test_disabled_mode_persists_zero_across_restart() -> None:
    config = "A;B;SA0u;RB0;M;"
    with StubProc(device_config=config) as proc:
        device = Device(proc)
        device.zcl_switch_binding_mode_set(1, DISABLED)
        assert device.read_zigbee_attr(
            1,
            ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
            ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_BINDING_MODE,
        ) == "0"

    with StubProc(device_config=config) as proc:
        device = Device(proc)
        assert device.read_zigbee_attr(
            1,
            ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG,
            ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_BINDING_MODE,
        ) == "0"


def test_disabled_mode_blocks_onoff_but_keeps_local_relay_click() -> None:
    proc, device = _configured_device()
    try:
        device.zcl_switch_binding_mode_set(1, DISABLED)
        assert device.zcl_relay_get(2) == "0"

        device.clear_events()
        device.click_button("A0")

        # Pure relay still acts locally.
        assert device.zcl_relay_get(2) == "1"
        # Existing topology is harmless: no bound OnOff command is emitted.
        assert device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_ON_OFF) == []

        device.clear_events()
        device.click_button("A0")
        assert device.zcl_relay_get(2) == "0"
        assert device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_ON_OFF) == []
    finally:
        proc.stop()


def test_disabled_mode_blocks_level_move_and_stop_on_long_press() -> None:
    proc, device = _configured_device()
    try:
        device.zcl_switch_binding_mode_set(1, DISABLED)
        device.clear_events()
        device.long_click_button("A0", duration_ms=1000)

        # A hold must not mutate the local SHORT-mode relay and must emit no
        # Level Move/Stop even though a real Level binding is present.
        assert device.zcl_relay_get(2) == "0"
        assert device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_LEVEL_CONTROL) == []
        assert device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_ON_OFF) == []
    finally:
        proc.stop()


def test_enabled_short_mode_still_sends_expected_onoff_and_level_commands() -> None:
    """Guard against implementing pure-relay safety by breaking normal dimmers."""
    proc, device = _configured_device()
    try:
        device.zcl_switch_binding_mode_set(1, SHORT_PRESS)

        device.clear_events()
        device.click_button("A0")
        onoff = device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_ON_OFF)
        assert [event.cmd for event in onoff] == [ZCL_CMD_ONOFF_TOGGLE]

        device.clear_events()
        device.long_click_button("A0", duration_ms=1000)
        level = device.zcl_list_cmds(endpoint=1, cluster=ZCL_CLUSTER_LEVEL_CONTROL)
        assert [event.cmd for event in level] == [
            ZCL_CMD_LEVEL_MOVE_WITH_ON_OFF,
            ZCL_CMD_LEVEL_STOP_WITH_ON_OFF,
        ]
    finally:
        proc.stop()
