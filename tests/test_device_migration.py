"""One-shot firmware-side migration for the BSEED TS0726-3-BS swapped pins.

The device shipped with its LEFT and MIDDLE relay/indicator pins swapped in
the stored device config. The migration rewrites the stored config to the
canonical form and pre-seeds the two swapped relays to ``detached_on`` so the
mains contacts stay energised while the panel LEDs take over the old relay
role. It fires only on an exact stored-config match with no marker present.
"""

import os
import shutil
import struct
import subprocess
from pathlib import Path
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
UNRELATED_CONFIG = "StubManufacturer;StubDevice;LC0;SA0u;RB0;IA1;M;"

# Config-string order: three switches (EP1-3), then three relays (EP4-6).
RELAY_LEFT_ENDPOINT = 4  # relay_idx 0, swapped
RELAY_MIDDLE_ENDPOINT = 5  # relay_idx 1, swapped
RELAY_RIGHT_ENDPOINT = 6  # relay_idx 2, canonical in both forms

NV_DEVICE_CONFIG_ITEM = 0x02
DEVICE_CONFIG_NV_SIZE = 2 + 128  # uint16 length prefix + data

STUB_BINARY = Path("build/stub/stub_device")


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


@pytest.fixture(scope="module", autouse=True)
def restore_plain_stub() -> Iterator[None]:
    yield
    if STUB_BINARY.exists():
        build_stub()


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


def seed_nv_device_config(config: str) -> None:
    """Pretend the stored config is ``config`` (simulates a manual edit)."""
    nvm_dir = Path("stub_nvm_data")
    nvm_dir.mkdir(exist_ok=True)
    payload = struct.pack("<H", len(config)) + config.encode()
    payload += bytes(DEVICE_CONFIG_NV_SIZE - len(payload))
    (nvm_dir / f"item_{NV_DEVICE_CONFIG_ITEM:02x}.bin").write_bytes(payload)


def test_migration_rewrites_config_and_preseeds_swapped_relays() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )

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
        assert (
            read_physical_mode(device, RELAY_RIGHT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )


def test_migration_is_one_shot_even_if_config_is_reverted_by_hand() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )

    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == CANONICAL_CONFIG

    # A later manual write of the swapped config must NOT re-trigger the
    # migration: the marker makes it strictly one-shot.
    seed_nv_device_config(SWAPPED_CONFIG)

    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == SWAPPED_CONFIG


def test_migration_skips_unrelated_configs() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )

    with StubProc(device_config=UNRELATED_CONFIG) as proc:
        device = Device(proc)

        assert read_config(device) == UNRELATED_CONFIG
        assert (
            read_physical_mode(device, 2) == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )


def test_plain_build_does_not_migrate() -> None:
    build_stub()

    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)

        assert read_config(device) == SWAPPED_CONFIG
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )


def test_revert_image_restores_swapped_config_and_clears_preseed() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == CANONICAL_CONFIG

    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG,
        MIGRATION_TO_CONFIG=CANONICAL_CONFIG,
        MIGRATION_REVERT="1",
    )
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)

        assert read_config(device) == SWAPPED_CONFIG
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )
        assert (
            read_physical_mode(device, RELAY_MIDDLE_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )

    # The revert image contains no forward migration, so a further boot keeps
    # the restored swapped config.
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == SWAPPED_CONFIG


def test_revert_image_leaves_foreign_configs_untouched() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG
    )
    with StubProc(device_config=SWAPPED_CONFIG) as proc:
        Device(proc)

    # Simulate the user editing the config after the migration ran.
    seed_nv_device_config(UNRELATED_CONFIG)

    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG,
        MIGRATION_TO_CONFIG=CANONICAL_CONFIG,
        MIGRATION_REVERT="1",
    )
    with StubProc(device_config=UNRELATED_CONFIG) as proc:
        device = Device(proc)
        assert read_config(device) == UNRELATED_CONFIG
