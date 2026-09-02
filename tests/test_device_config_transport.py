"""Transactional chunked device_config transport tests."""

import struct
from pathlib import Path

from tests.client import StubProc
from tests.conftest import Device
from tests.zcl_consts import (
    ZCL_ATTR_BASIC_DEVICE_CONFIG,
    ZCL_CLUSTER_BASIC,
    ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
    ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE,
)

INITIAL = "TestManufacturer;TestDev;SA0u;RB0;IA1;M;"
CANONICAL = (
    "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;"
    "SB4u;RD2;IB5;M;"
)
NV_CONFIG_ITEM = 0x02
CHUNK = 24


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def stage(device: Device, tx: int, data: bytes, skip_offset: int | None = None) -> None:
    for offset in range(0, len(data), CHUNK):
        chunk = data[offset : offset + CHUNK]
        if skip_offset == offset:
            continue
        payload = bytes([tx, offset, len(chunk)]) + chunk
        device.call_zigbee_cmd(
            1, ZCL_CLUSTER_BASIC, ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE, payload
        )


def commit_payload(tx: int, data: bytes, crc: int | None = None) -> bytes:
    value = crc16(data) if crc is None else crc
    return bytes([tx, len(data), value & 0xFF, value >> 8])


def read_config(device: Device) -> str:
    return device.read_zigbee_attr(
        1, ZCL_CLUSTER_BASIC, ZCL_ATTR_BASIC_DEVICE_CONFIG
    )


def test_full_canonical_value_round_trips_unchanged() -> None:
    data = CANONICAL.encode()
    assert len(data) > 64  # proves this is the formerly oversized real shape

    with StubProc(device_config=INITIAL) as proc:
        d = Device(proc)
        stage(d, 7, data)
        d.call_zigbee_cmd(
            1,
            ZCL_CLUSTER_BASIC,
            ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
            commit_payload(7, data),
        )
        assert read_config(d) == CANONICAL

        raw = (Path("stub_nvm_data") / f"item_{NV_CONFIG_ITEM:02x}.bin").read_bytes()
        stored_len = struct.unpack("<H", raw[:2])[0]
        assert stored_len == len(data)
        assert raw[2 : 2 + stored_len] == data


def test_missing_chunk_never_mutates_nvm() -> None:
    data = CANONICAL.encode()
    with StubProc(device_config=INITIAL) as proc:
        d = Device(proc)
        before = read_config(d)
        stage(d, 9, data, skip_offset=CHUNK)
        result = d.call_zigbee_cmd_raw(
            1,
            ZCL_CLUSTER_BASIC,
            ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
            commit_payload(9, data),
        )
        assert result["result"] == "ACTION_DENIED"
        assert read_config(d) == before


def test_bad_crc_is_rejected_and_stage_is_discarded() -> None:
    data = CANONICAL.encode()
    with StubProc(device_config=INITIAL) as proc:
        d = Device(proc)
        before = read_config(d)
        stage(d, 11, data)
        result = d.call_zigbee_cmd_raw(
            1,
            ZCL_CLUSTER_BASIC,
            ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
            commit_payload(11, data, crc16(data) ^ 0xFFFF),
        )
        assert result["result"] == "INVALID_VALUE"
        assert read_config(d) == before

        # A commit cannot reuse bytes from the discarded failed transaction.
        retry = d.call_zigbee_cmd_raw(
            1,
            ZCL_CLUSTER_BASIC,
            ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
            commit_payload(11, data),
        )
        assert retry["result"] == "ACTION_DENIED"
        assert read_config(d) == before


def test_truncated_or_structurally_invalid_config_is_rejected() -> None:
    for candidate in (
        CANONICAL[:-1],  # missing terminal semicolon
        "iedhxgyi;;RC2;",  # empty mandatory token
        "iedhxgyi;TS0726-3-BS;RC2;\x00BAD;",  # embedded NUL
    ):
        data = candidate.encode()
        with StubProc(device_config=INITIAL) as proc:
            d = Device(proc)
            before = read_config(d)
            stage(d, 13, data)
            result = d.call_zigbee_cmd_raw(
                1,
                ZCL_CLUSTER_BASIC,
                ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT,
                commit_payload(13, data),
            )
            assert result["result"] == "INVALID_VALUE"
            assert read_config(d) == before


def test_nonzero_offset_cannot_start_new_transaction() -> None:
    data = CANONICAL.encode()
    with StubProc(device_config=INITIAL) as proc:
        d = Device(proc)
        chunk = data[CHUNK : CHUNK * 2]
        result = d.call_zigbee_cmd_raw(
            1,
            ZCL_CLUSTER_BASIC,
            ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE,
            bytes([21, CHUNK, len(chunk)]) + chunk,
        )
        assert result["result"] == "ACTION_DENIED"
        assert read_config(d) == INITIAL


def test_chunk_length_and_bounds_are_enforced() -> None:
    with StubProc(device_config=INITIAL) as proc:
        d = Device(proc)

        too_big = bytes([31, 0, 25]) + b"A" * 25
        result = d.call_zigbee_cmd_raw(
            1, ZCL_CLUSTER_BASIC, ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE, too_big
        )
        assert result["result"] == "INVALID_VALUE"

        mismatched = bytes([31, 0, 4]) + b"ABC"
        result = d.call_zigbee_cmd_raw(
            1, ZCL_CLUSTER_BASIC, ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE, mismatched
        )
        assert result["result"] == "INVALID_VALUE"
        assert read_config(d) == INITIAL
