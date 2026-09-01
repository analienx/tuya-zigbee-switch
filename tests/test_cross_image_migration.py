"""Cross-image proof: BSEED migration is a bootstrap, not a fork dependency.

Phase A boots the migration image over the historical swapped state and
asserts the canonical config + persisted physical policies.
Phase B rebuilds the SAME source tree as a plain generic image (no
DEVICE_MIGRATION_* defines, no migration code) and boots the SAME NVM:
everything must keep working with ordinary generic logic, and no migration
mutation may occur. (Ruling 5490809468 section 9 — hard acceptance
requirement before real OTA.)
"""

import os
import pathlib
import subprocess
import shutil
from typing import Iterator

import pytest

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_BASIC_DEVICE_CONFIG,
    ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
    ZCL_CLUSTER_BASIC,
    ZCL_CLUSTER_ON_OFF,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED,
    ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON,
)

SWAPPED_CONFIG = (
    "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;"
)
CANONICAL_CONFIG = (
    "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;"
)

RELAY_LEFT_ENDPOINT = 4
RELAY_MIDDLE_ENDPOINT = 5
RELAY_RIGHT_ENDPOINT = 6

NV_CONFIG_ITEM = 0x02
NV_MARKER_ITEM = 0x28
NV_PHYSICAL_MODE_BASE = 0x23

MIG_FORWARD_COMPLETE = 2


def build_stub(**flags: str | None) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is required to build the stub device")
    env = dict(os.environ)
    env.update({key: value for key, value in flags.items() if value is not None})
    subprocess.run(
        ["make", "-C", "src/stub", "build"],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )


@pytest.fixture()
def migration_image() -> Iterator[None]:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )
    yield


def plain_image() -> None:
    build_stub()  # plain generic build: no migration defines at all


def read_nv(item: int) -> bytes | None:
    path = pathlib.Path("stub_nvm_data") / f"item_{item:02x}.bin"
    return path.read_bytes() if path.exists() else None


def read_config(device: Device) -> str:
    return device.read_zigbee_attr(
        1, ZCL_CLUSTER_BASIC, ZCL_ATTR_BASIC_DEVICE_CONFIG
    )


def read_physical_mode(device: Device, endpoint: int) -> int:
    return int(
        device.read_zigbee_attr(
            endpoint, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE
        )
    )


def test_migration_then_plain_generic_image(migration_image) -> None:
    # ---- Phase A: BSEED migration image crosses the swapped->canonical gap
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == CANONICAL_CONFIG
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )
        assert (
            read_physical_mode(device, RELAY_MIDDLE_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )
        marker_before = read_nv(NV_MARKER_ITEM)
        assert marker_before is not None

    # ---- Phase B: plain generic image, SAME NVM, no migration code.
    # device_config=None so the stub does NOT re-seed the config: the plain
    # image must boot exactly what Phase A persisted.
    plain_image()
    with StubProc() as proc:
        device = Device(proc)

        # Ordinary generic logic, no migration mutation:
        assert read_config(device) == CANONICAL_CONFIG
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )
        assert (
            read_physical_mode(device, RELAY_MIDDLE_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )
        assert (
            read_physical_mode(device, 6)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )
        assert read_nv(NV_MARKER_ITEM) == marker_before

        # The plain image must not even carry the migration strings.
        # (Verified below via the binary; here via behavior: the device
        # boots, endpoints exist, and everything is generic.)

    # Binary-level proof: the plain build contains no migration code
    # (neither the forward nor the revert transaction strings).
    binary = pathlib.Path("build/stub/stub_device").read_bytes()
    assert b"swapped-pin migration complete" not in binary
    assert b"swapped-pin state restored" not in binary


def test_plain_generic_image_first_enable_and_behavior(migration_image) -> None:
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        Device(proc)  # migrate

    plain_image()
    with StubProc() as proc:
        device = Device(proc)

        # First physical output enable for LEFT/MIDDLE is already ON.
        res = device.p.exec("read_pin_init 34")  # C2 = (2<<4)|2
        assert res.ok and int(res.payload["value"]) == 1
        res = device.p.exec("read_pin_init 35")  # C3 = (2<<4)|3
        assert res.ok and int(res.payload["value"]) == 1
        assert device.get_gpio("C2", refresh=True)
        assert device.get_gpio("C3", refresh=True)

        # Virtual relay state changes independently of the physical output.
        device.zcl_relay_off(RELAY_LEFT_ENDPOINT)
        assert device.zcl_relay_get(RELAY_LEFT_ENDPOINT) == "0"
        assert device.get_gpio("C2", refresh=True)
        device.zcl_relay_on(RELAY_LEFT_ENDPOINT)
        assert device.zcl_relay_get(RELAY_LEFT_ENDPOINT) == "1"
        assert device.get_gpio("C2", refresh=True)
