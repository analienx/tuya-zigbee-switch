"""Tests for physical relay modes that decouple mains power from virtual state."""

from typing import Iterator

import pytest

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
    ZCL_CLUSTER_ON_OFF,
    ZCL_ONOFF_CONFIGURATION_RELAY_MODE_SHORT,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON,
)


DEVICE_CONFIG = "X;Y;SA0u;RB0;IA1;M;"
SWITCH_ENDPOINT = 1
RELAY_ENDPOINT = 2
BUTTON_PIN = "A0"
RELAY_PIN = "B0"
INDICATOR_PIN = "A1"


@pytest.fixture()
def device() -> Iterator[Device]:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        yield Device(proc)


def set_physical_mode(device: Device, mode: int) -> None:
    device.write_zigbee_attr(
        RELAY_ENDPOINT,
        ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
        mode,
    )


def test_detached_on_pins_physical_relay_while_virtual_state_changes(
    device: Device,
) -> None:
    set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    assert device.get_gpio(RELAY_PIN, refresh=True)
    assert device.zcl_relay_get(RELAY_ENDPOINT) == "0"

    device.zcl_relay_on(RELAY_ENDPOINT)
    assert device.zcl_relay_get(RELAY_ENDPOINT) == "1"
    assert device.get_gpio(RELAY_PIN, refresh=True)
    assert device.get_gpio(INDICATOR_PIN, refresh=True)

    device.zcl_relay_off(RELAY_ENDPOINT)
    assert device.zcl_relay_get(RELAY_ENDPOINT) == "0"
    assert device.get_gpio(RELAY_PIN, refresh=True)
    assert not device.get_gpio(INDICATOR_PIN, refresh=True)


def test_detached_off_pins_physical_relay_while_virtual_state_changes(
    device: Device,
) -> None:
    set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF)

    assert not device.get_gpio(RELAY_PIN, refresh=True)

    device.zcl_relay_on(RELAY_ENDPOINT)
    assert device.zcl_relay_get(RELAY_ENDPOINT) == "1"
    assert not device.get_gpio(RELAY_PIN, refresh=True)
    assert device.get_gpio(INDICATOR_PIN, refresh=True)


def test_reattach_synchronizes_physical_relay_to_virtual_state(
    device: Device,
) -> None:
    set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    device.zcl_relay_off(RELAY_ENDPOINT)

    assert device.zcl_relay_get(RELAY_ENDPOINT) == "0"
    assert device.get_gpio(RELAY_PIN, refresh=True)

    set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED)

    assert not device.get_gpio(RELAY_PIN, refresh=True)


def test_short_press_toggles_virtual_state_and_indicator_but_not_mains(
    device: Device,
) -> None:
    set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    device.zcl_switch_relay_mode_set(
        SWITCH_ENDPOINT, ZCL_ONOFF_CONFIGURATION_RELAY_MODE_SHORT
    )

    device.click_button(BUTTON_PIN)

    assert device.zcl_relay_get(RELAY_ENDPOINT) == "1"
    assert device.get_gpio(RELAY_PIN, refresh=True)
    assert device.get_gpio(INDICATOR_PIN, refresh=True)

    device.click_button(BUTTON_PIN)

    assert device.zcl_relay_get(RELAY_ENDPOINT) == "0"
    assert device.get_gpio(RELAY_PIN, refresh=True)
    assert not device.get_gpio(INDICATOR_PIN, refresh=True)


def test_detached_on_mode_survives_reboot() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        device = Device(proc)
        set_physical_mode(device, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
        device.zcl_relay_off(RELAY_ENDPOINT)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        device = Device(proc)

        assert (
            device.read_zigbee_attr(
                RELAY_ENDPOINT,
                ZCL_CLUSTER_ON_OFF,
                ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
            )
            == str(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
        )
        assert device.zcl_relay_get(RELAY_ENDPOINT) == "0"
        assert device.get_gpio(RELAY_PIN, refresh=True)
