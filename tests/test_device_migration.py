"""Crash-safe transaction tests for the BSEED TS0726-3-BS swapped-pin migration.

The firmware-side migration is a phased transaction (see
``src/device_config/device_migration.c``): every durable write boundary must
leave the NVM in a state that is safe under either pin map, and every crash or
injected NVM failure must resume to a safe state on the next boot.

NV layout used by the tests (see ``src/device_config/nvm_items.h``):

- item 0x02: device config (uint16 length prefix + data, 130 bytes total)
- item 0x09 + relay_idx: relay cluster record (on_off, startup_mode,
  indicator_led_mode, indicator_led_on)
- item 0x23 + relay_idx: physical relay mode (uint8)
- item 0x28: migration marker (uint32 state)
"""

import os
import shutil
import struct
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_BASIC_DEVICE_CONFIG,
    ZCL_ATTR_ONOFF,
    ZCL_ATTR_ONOFF_INDICATOR_MODE,
    ZCL_ATTR_ONOFF_INDICATOR_STATE,
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

# Endpoints: three switches (EP1-3), then three relays (EP4-6).
RELAY_LEFT_ENDPOINT = 4  # relay_idx 0, swapped
RELAY_MIDDLE_ENDPOINT = 5  # relay_idx 1, swapped
RELAY_RIGHT_ENDPOINT = 6  # relay_idx 2, canonical in both forms

INDICATOR_MODE_MANUAL = 2
INDICATOR_MODE_SAME = 0
INDICATOR_ON = 1

# Mains (physically energised) and panel-LED pins per pin map.
MAINS_LEFT_PIN = "C2"
MAINS_MIDDLE_PIN = "C3"
LED_LEFT_PIN = "C0"
LED_MIDDLE_PIN = "D7"

NV_CONFIG_ITEM = 0x02
NV_CONFIG_SIZE = 2 + 128
NV_RELAY_RECORD_BASE = 0x09  # 3 + MAX_SWITCHES(5) + 1 + relay_idx
NV_PHYSICAL_MODE_BASE = 0x23  # 35 + relay_idx
NV_MARKER_ITEM = 0x28  # 40

MIG_NONE = 0
MIG_FORWARD_IN_PROGRESS = 1
MIG_FORWARD_COMPLETE = 2
MIG_REVERT_IN_PROGRESS = 3

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


@pytest.fixture(scope="module")
def forward_stub() -> Iterator[None]:
    build_stub(MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG)
    yield
    build_stub()


@contextmanager
def booted(device_config: str | None = None) -> Iterator[Device]:
    """Boot the stub. ``device_config=None`` keeps whatever NV already holds."""
    with StubProc(device_config=device_config) as proc:
        yield Device(proc)


def nv_path(item: int) -> Path:
    return Path("stub_nvm_data") / f"item_{item:02x}.bin"


def write_nv(item: int, payload: bytes) -> None:
    path = nv_path(item)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)


def read_nv(item: int) -> bytes | None:
    path = nv_path(item)
    return path.read_bytes() if path.exists() else None


def seed_config(config: str) -> None:
    payload = struct.pack("<H", len(config)) + config.encode()
    write_nv(NV_CONFIG_ITEM, payload.ljust(NV_CONFIG_SIZE, b"\x00"))


def seed_marker(state: int) -> None:
    write_nv(NV_MARKER_ITEM, struct.pack("<I", state))


def read_marker() -> int | None:
    raw = read_nv(NV_MARKER_ITEM)
    return struct.unpack("<I", raw)[0] if raw is not None else None


def seed_relay_record(
    relay_idx: int,
    on_off: int = 0,
    startup_mode: int = 0,
    indicator_mode: int = INDICATOR_MODE_SAME,
    indicator_on: int = 0,
) -> None:
    write_nv(
        NV_RELAY_RECORD_BASE + relay_idx,
        bytes([on_off, startup_mode, indicator_mode, indicator_on]),
    )


def seed_physical_mode(relay_idx: int, mode: int) -> None:
    write_nv(NV_PHYSICAL_MODE_BASE + relay_idx, bytes([mode]))


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


def read_indicator_mode(device: Device, endpoint: int) -> int:
    return int(
        device.read_zigbee_attr(
            endpoint, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_INDICATOR_MODE
        )
    )


def read_indicator_state(device: Device, endpoint: int) -> int:
    return int(
        device.read_zigbee_attr(
            endpoint, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_INDICATOR_STATE
        )
    )


def assert_forward_complete(device: Device) -> None:
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
    assert read_marker() == MIG_FORWARD_COMPLETE


def test_forward_migration_full_transaction(forward_stub: None) -> None:
    with booted(SWAPPED_CONFIG) as device:
        assert_forward_complete(device)
        assert (
            read_indicator_mode(device, RELAY_LEFT_ENDPOINT)
            == INDICATOR_MODE_MANUAL
        )
        assert (
            read_indicator_state(device, RELAY_LEFT_ENDPOINT) == INDICATOR_ON
        )
        assert (
            read_indicator_mode(device, RELAY_MIDDLE_ENDPOINT)
            == INDICATOR_MODE_MANUAL
        )
        assert (
            read_indicator_state(device, RELAY_MIDDLE_ENDPOINT) == INDICATOR_ON
        )
        # Mains pinned energised by detached_on even with the virtual state off.
        assert device.get_gpio(MAINS_LEFT_PIN, refresh=True)
        assert device.get_gpio(MAINS_MIDDLE_PIN, refresh=True)
        device.zcl_relay_off(RELAY_LEFT_ENDPOINT)
        assert device.get_gpio(MAINS_LEFT_PIN, refresh=True)


def test_forward_is_one_shot_for_hand_reverted_config(forward_stub: None) -> None:
    with booted(SWAPPED_CONFIG):
        assert read_marker() == MIG_FORWARD_COMPLETE

    # A later manual write of the swapped config must NOT re-trigger the
    # migration: FORWARD_COMPLETE makes it strictly one-shot.
    seed_config(SWAPPED_CONFIG)

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() == MIG_FORWARD_COMPLETE


def test_forward_resumes_after_crash_after_in_progress(forward_stub: None) -> None:
    seed_config(SWAPPED_CONFIG)
    seed_marker(MIG_FORWARD_IN_PROGRESS)

    with booted() as device:
        assert_forward_complete(device)


def test_forward_resumes_after_crash_after_left_mode_write(
    forward_stub: None,
) -> None:
    seed_config(SWAPPED_CONFIG)
    seed_marker(MIG_FORWARD_IN_PROGRESS)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with booted() as device:
        assert_forward_complete(device)


def test_forward_resumes_after_crash_after_both_mode_writes(
    forward_stub: None,
) -> None:
    seed_config(SWAPPED_CONFIG)
    seed_marker(MIG_FORWARD_IN_PROGRESS)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    with booted() as device:
        assert_forward_complete(device)


def test_forward_resumes_after_crash_after_config_write(forward_stub: None) -> None:
    seed_config(CANONICAL_CONFIG)
    seed_marker(MIG_FORWARD_IN_PROGRESS)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    # Canonical + IN_PROGRESS must NOT be treated as a skip: the transaction
    # re-ensures safety and then completes.
    with booted() as device:
        assert_forward_complete(device)


def test_forward_does_not_complete_for_foreign_config(forward_stub: None) -> None:
    seed_config(UNRELATED_CONFIG)
    seed_marker(MIG_FORWARD_IN_PROGRESS)

    with booted() as device:
        # Protected invariant failure: never silently mark complete.
        assert read_config(device) == UNRELATED_CONFIG
        assert read_marker() == MIG_FORWARD_IN_PROGRESS
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None


def test_forward_skips_fresh_canonical_device(forward_stub: None) -> None:
    seed_config(CANONICAL_CONFIG)

    with booted() as device:
        # A factory-canonical device (no marker) is not ours to touch.
        assert read_config(device) == CANONICAL_CONFIG
        assert read_marker() is None
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None


def test_forward_retries_when_config_write_fails(
    forward_stub: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Write #1 to item 0x02 is the stub's own config seed; #2 is the
    # migration's canonical write.
    monkeypatch.setenv("STUB_NVM_FAIL_WRITE", "2@2")

    with booted(SWAPPED_CONFIG) as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() == MIG_FORWARD_IN_PROGRESS
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )

    monkeypatch.delenv("STUB_NVM_FAIL_WRITE")

    with booted() as device:
        assert_forward_complete(device)


def test_forward_retries_when_physical_mode_write_fails(
    forward_stub: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_NVM_FAIL_WRITE", "0x23@1")

    with booted(SWAPPED_CONFIG) as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() == MIG_FORWARD_IN_PROGRESS
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None

    monkeypatch.delenv("STUB_NVM_FAIL_WRITE")

    with booted() as device:
        assert_forward_complete(device)


def test_forward_retries_when_complete_marker_write_fails(
    forward_stub: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Marker writes: #1 = FORWARD_IN_PROGRESS, #2 = FORWARD_COMPLETE.
    monkeypatch.setenv("STUB_NVM_FAIL_WRITE", "40@2")

    with booted(SWAPPED_CONFIG) as device:
        assert read_config(device) == CANONICAL_CONFIG
        assert read_marker() == MIG_FORWARD_IN_PROGRESS
        assert (
            read_physical_mode(device, RELAY_MIDDLE_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON
        )

    monkeypatch.delenv("STUB_NVM_FAIL_WRITE")

    with booted() as device:
        assert_forward_complete(device)


def test_plain_build_does_not_migrate() -> None:
    build_stub()  # plain build: no migration defines

    with booted(SWAPPED_CONFIG) as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() is None
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )


def build_forward_image() -> None:
    build_stub(MIGRATION_FROM_CONFIG=SWAPPED_CONFIG, MIGRATION_TO_CONFIG=CANONICAL_CONFIG)


def build_revert_image() -> None:
    build_stub(
        MIGRATION_FROM_CONFIG=SWAPPED_CONFIG,
        MIGRATION_TO_CONFIG=CANONICAL_CONFIG,
        MIGRATION_REVERT="1",
    )


def run_forward_migration() -> None:
    """Build the forward image and migrate a fresh swapped device."""
    build_forward_image()
    with booted(SWAPPED_CONFIG):
        assert read_marker() == MIG_FORWARD_COMPLETE


def test_revert_restores_swapped_state() -> None:
    run_forward_migration()
    build_revert_image()

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert (
            read_physical_mode(device, RELAY_LEFT_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )
        assert (
            read_physical_mode(device, RELAY_MIDDLE_ENDPOINT)
            == ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED
        )
        assert (
            read_indicator_mode(device, RELAY_LEFT_ENDPOINT)
            == INDICATOR_MODE_MANUAL
        )
        assert (
            read_indicator_state(device, RELAY_LEFT_ENDPOINT) == INDICATOR_ON
        )
        assert read_marker() is None
        # Mains stays energised via MANUAL + ON indicator; LEDs off (attached).
        assert device.get_gpio(MAINS_LEFT_PIN, refresh=True)
        assert device.get_gpio(MAINS_MIDDLE_PIN, refresh=True)
        assert not device.get_gpio(LED_LEFT_PIN, refresh=True)
        assert not device.get_gpio(LED_MIDDLE_PIN, refresh=True)


def test_revert_forces_indicator_safety_after_same_mode() -> None:
    run_forward_migration()

    # During canonical operation the operator changes the indicator mode to
    # SAME on EP4 (the exact hazard precondition for rollback).
    build_forward_image()
    with booted() as device:
        device.write_zigbee_attr(
            RELAY_LEFT_ENDPOINT, ZCL_CLUSTER_ON_OFF,
            ZCL_ATTR_ONOFF_INDICATOR_MODE, INDICATOR_MODE_SAME,
        )

    build_revert_image()
    with booted() as device:
        # Revert must restore MANUAL + ON BEFORE mapping the indicator back
        # onto the mains relays.
        assert (
            read_indicator_mode(device, RELAY_LEFT_ENDPOINT)
            == INDICATOR_MODE_MANUAL
        )
        assert (
            read_indicator_state(device, RELAY_LEFT_ENDPOINT) == INDICATOR_ON
        )
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() is None
        assert device.get_gpio(MAINS_LEFT_PIN, refresh=True)


def test_revert_resumes_after_in_progress() -> None:
    seed_config(CANONICAL_CONFIG)
    seed_marker(MIG_REVERT_IN_PROGRESS)
    seed_relay_record(0, indicator_mode=INDICATOR_MODE_SAME)
    seed_relay_record(1, indicator_mode=INDICATOR_MODE_SAME)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    build_revert_image()
    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert (
            read_indicator_mode(device, RELAY_LEFT_ENDPOINT)
            == INDICATOR_MODE_MANUAL
        )
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_marker() is None


def test_revert_resumes_after_safety_write() -> None:
    seed_config(CANONICAL_CONFIG)
    seed_marker(MIG_REVERT_IN_PROGRESS)
    seed_relay_record(0, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_relay_record(1, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    build_revert_image()
    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_marker() is None


def test_revert_resumes_after_swapped_config_write() -> None:
    seed_config(SWAPPED_CONFIG)
    seed_marker(MIG_REVERT_IN_PROGRESS)
    seed_relay_record(0, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_relay_record(1, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_physical_mode(0, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    build_revert_image()
    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_marker() is None


def test_revert_resumes_after_partial_mode_delete() -> None:
    seed_config(SWAPPED_CONFIG)
    seed_marker(MIG_REVERT_IN_PROGRESS)
    seed_relay_record(0, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_relay_record(1, indicator_mode=INDICATOR_MODE_MANUAL, indicator_on=1)
    seed_physical_mode(1, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)

    build_revert_image()
    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_nv(NV_PHYSICAL_MODE_BASE + 1) is None
        assert read_marker() is None


def test_revert_retries_when_mode_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_forward_migration()
    build_revert_image()

    monkeypatch.setenv("STUB_NVM_FAIL_DELETE", "0x23@1")

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() == MIG_REVERT_IN_PROGRESS
        assert read_nv(NV_PHYSICAL_MODE_BASE) is not None

    monkeypatch.delenv("STUB_NVM_FAIL_DELETE")

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_marker() is None


def test_revert_retries_when_marker_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_forward_migration()
    build_revert_image()

    monkeypatch.setenv("STUB_NVM_FAIL_DELETE", "40@1")

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_nv(NV_PHYSICAL_MODE_BASE) is None
        assert read_marker() == MIG_REVERT_IN_PROGRESS

    monkeypatch.delenv("STUB_NVM_FAIL_DELETE")

    with booted() as device:
        assert read_config(device) == SWAPPED_CONFIG
        assert read_marker() is None



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

