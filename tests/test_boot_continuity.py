"""Boot-continuity tests for the generic physical-relay-policy feature.

Invariant under test: for a persisted `detached_on` policy the FIRST output
enable already carries the electrical ON level — the boot sequence must never
enable the contact at the inactive level and flip it afterwards. The stub
records the first enabled level per pin, exposed via the `read_pin_init`
REPL command, which distinguishes "enabled ON" from "enabled LOW then set
HIGH" (the latter is a glitch).
"""

import pathlib

import pytest

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_ONOFF_TOGGLE,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON,
)

DEVICE_CONFIG = "TestManufacturer;TestDev;SA0u;RB0;IA1;M;"
LATCHING_DEVICE_CONFIG = "TestManufacturer;TestDev;SA0u;RB0B3;IA1;M;"

SWITCH_ENDPOINT = 1
RELAY_ENDPOINT = 2
RELAY_PIN = "B0"
RELAY_OFF_COIL_PIN = "B3"
INDICATOR_PIN = "A1"
BUTTON_PIN = "A0"

NV_RELAY_RECORD_BASE = 0x09  # 3 + MAX_SWITCHES(5) + 1 + relay_idx
NV_PHYSICAL_MODE_BASE = 0x23  # 35 + relay_idx

# ZCL start-up OnOff modes (src/zigbee/consts.h)
STARTUP_OFF = 0x00
STARTUP_ON = 0x01
STARTUP_TOGGLE = 0x02
STARTUP_PREVIOUS = 0xFF


@pytest.fixture()
def device() -> Iterator[Device]:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        yield Device(proc)


@pytest.fixture()
def latching_device() -> Iterator[Device]:
    with StubProc(device_config=LATCHING_DEVICE_CONFIG) as proc:
        yield Device(proc)


def seed_physical_mode(mode: int) -> None:
    pathlib.Path("stub_nvm_data").mkdir(exist_ok=True)
    pathlib.Path("stub_nvm_data/item_23.bin").write_bytes(bytes([mode]))


def seed_relay_record(on_off: int, startup_mode: int) -> None:
    pathlib.Path("stub_nvm_data").mkdir(exist_ok=True)
    # NV_RELAY_RECORD_BASE + relay 0: on_off, startup_mode, ind_mode, ind_on
    pathlib.Path("stub_nvm_data/item_09.bin").write_bytes(
        bytes([on_off, startup_mode, 0, 0])
    )


def read_physical_mode(device: Device) -> int:
    return int(
        device.read_zigbee_attr(
            RELAY_ENDPOINT,
            ZCL_CLUSTER_ON_OFF,
            ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
        )
    )


def write_physical_mode(device: Device, mode: int) -> None:
    device.write_zigbee_attr(
        RELAY_ENDPOINT,
        ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
        mode,
    )


def first_enable_level(device: Device, pin: str) -> int:
    """Level the pin carried at its FIRST output enable (stub-recorded)."""
    res = device.p.exec(f"read_pin_init {device._parse_pin(pin)}")
    assert res.ok, f"pin {pin} was never enabled as output"
    return int(res.payload["value"])


def test_missing_physical_mode_nvm_boots_attached_legacy(device: Device) -> None:
    assert read_physical_mode(device) == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
    # Legacy attached boot: startup OFF -> contact OFF, then ordinary drives.
    assert not device.get_gpio(RELAY_PIN, refresh=True)
    device.zcl_relay_on(RELAY_ENDPOINT)
    assert device.get_gpio(RELAY_PIN, refresh=True)


def test_attached_mode_keeps_legacy_startup(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert read_physical_mode(d) == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        d.zcl_relay_on(RELAY_ENDPOINT)
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_detached_on_first_enable_is_on(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert first_enable_level(d, RELAY_PIN) == 1
        assert d.get_gpio(RELAY_PIN, refresh=True)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "0"

        # Zigbee OFF/ON change virtual state only; physical stays ON.
        d.zcl_relay_off(RELAY_ENDPOINT)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "0"
        assert d.get_gpio(RELAY_PIN, refresh=True)
        d.zcl_relay_on(RELAY_ENDPOINT)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "1"
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_detached_off_first_enable_is_off(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert first_enable_level(d, RELAY_PIN) == 0
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        d.zcl_relay_on(RELAY_ENDPOINT)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "1"
        assert not d.get_gpio(RELAY_PIN, refresh=True)


def test_detached_on_toggle_is_virtual_only(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.call_zigbee_cmd(
            RELAY_ENDPOINT, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_TOGGLE
        )
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "1"
        assert d.get_gpio(RELAY_PIN, refresh=True)
        d.call_zigbee_cmd(
            RELAY_ENDPOINT, ZCL_CLUSTER_ON_OFF, ZCL_CMD_ONOFF_TOGGLE
        )
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "0"
        assert d.get_gpio(RELAY_PIN, refresh=True)
        # Indicator may still track the virtual state (default SAME mode).
        assert d.get_gpio(INDICATOR_PIN, refresh=True) == (
            d.zcl_relay_get(RELAY_ENDPOINT) == "1"
        )


def test_local_button_is_virtual_only_while_detached(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.click_button(BUTTON_PIN)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "1"
        assert d.get_gpio(RELAY_PIN, refresh=True)
        d.click_button(BUTTON_PIN)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "0"
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_detached_on_persists_across_reboot() -> None:
    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        write_physical_mode(d, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert (
            read_physical_mode(d) == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )
        # Reboot persistence: the first enable is already ON.
        assert first_enable_level(d, RELAY_PIN) == 1
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_invalid_nvm_byte_boots_attached_without_forced_state(
    device: Device,
) -> None:
    seed_physical_mode(0xAB)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert read_physical_mode(d) == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        assert not d.get_gpio(RELAY_PIN, refresh=True)


def test_reattach_synchronizes_to_virtual_state(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.zcl_relay_off(RELAY_ENDPOINT)  # virtual OFF, physical pinned ON
        assert d.get_gpio(RELAY_PIN, refresh=True)
        write_physical_mode(d, ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED)
        assert not d.get_gpio(RELAY_PIN, refresh=True)


def test_reattach_synchronizes_to_virtual_on(device: Device) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        d.zcl_relay_on(RELAY_ENDPOINT)  # virtual ON, physical pinned ON
        assert d.get_gpio(RELAY_PIN, refresh=True)
        write_physical_mode(d, ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED)
        assert d.get_gpio(RELAY_PIN, refresh=True)


def test_latching_detached_on_issues_single_policy_pulse(
    latching_device: Device,
) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with StubProc(device_config=LATCHING_DEVICE_CONFIG) as proc:
        d = Device(proc)
        # The single boot policy pulse is in flight (frozen time): the ON
        # coil is driven and both coils return inactive after the pulse.
        assert d.get_gpio(RELAY_PIN, refresh=True)
        assert not d.get_gpio(RELAY_OFF_COIL_PIN, refresh=True)
        d.step_time(300)
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        assert not d.get_gpio(RELAY_OFF_COIL_PIN, refresh=True)

        # Virtual ON/OFF while detached does not generate coil pulses.
        d.zcl_relay_on(RELAY_ENDPOINT)
        d.zcl_relay_off(RELAY_ENDPOINT)
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        assert not d.get_gpio(RELAY_OFF_COIL_PIN, refresh=True)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == "0"


def test_latching_attached_boot_unchanged(latching_device: Device) -> None:
    # Legacy attached boot: startup OFF, no pulse, coils inactive.
    assert not latching_device.get_gpio(RELAY_PIN, refresh=True)
    assert not latching_device.get_gpio(RELAY_OFF_COIL_PIN, refresh=True)
    latching_device.zcl_relay_on(RELAY_ENDPOINT)
    assert latching_device.get_gpio(RELAY_PIN, refresh=True)
    latching_device.step_time(300)
    assert not latching_device.get_gpio(RELAY_PIN, refresh=True)
    assert not latching_device.get_gpio(RELAY_OFF_COIL_PIN, refresh=True)


@pytest.mark.parametrize(
    "startup_mode, expected_virtual",
    [
        (STARTUP_OFF, "0"),
        (STARTUP_ON, "1"),
        (STARTUP_TOGGLE, "0"),  # prev_on=1 -> toggle -> off
        (STARTUP_PREVIOUS, "1"),
    ],
)
def test_startup_matrix_detached_on(
    startup_mode: int, expected_virtual: str
) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_relay_record(on_off=1, startup_mode=startup_mode)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        # Physical policy is ON regardless of the startup-mode result.
        assert first_enable_level(d, RELAY_PIN) == 1
        assert d.get_gpio(RELAY_PIN, refresh=True)
        # Virtual state follows the startup-mode result.
        assert d.zcl_relay_get(RELAY_ENDPOINT) == expected_virtual


@pytest.mark.parametrize(
    "startup_mode, expected_virtual",
    [
        (STARTUP_OFF, "0"),
        (STARTUP_ON, "1"),
        (STARTUP_TOGGLE, "0"),
        (STARTUP_PREVIOUS, "1"),
    ],
)
def test_startup_matrix_detached_off(
    startup_mode: int, expected_virtual: str
) -> None:
    seed_physical_mode(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF)
    seed_relay_record(on_off=1, startup_mode=startup_mode)

    with StubProc(device_config=DEVICE_CONFIG) as proc:
        d = Device(proc)
        assert first_enable_level(d, RELAY_PIN) == 0
        assert not d.get_gpio(RELAY_PIN, refresh=True)
        assert d.zcl_relay_get(RELAY_ENDPOINT) == expected_virtual
