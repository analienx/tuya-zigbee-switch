"""Host-only stock-wire fixtures. No live breaker, MQTT or actuator access."""
import ctypes as c
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Channel(c.Structure):
    _fields_ = [("threshold", c.c_uint16), ("enabled", c.c_uint8),
                ("known", c.c_uint8)]


class State(c.Structure):
    _fields_ = [("channel", Channel * 5)]


@pytest.fixture(scope="module")
def codec(tmp_path_factory):
    source = ROOT / "src/base_components/tongou_threshold_codec.c"
    library = tmp_path_factory.mktemp("tongou_codec") / "codec.so"
    subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                    "-shared", "-fPIC", str(source), "-o", str(library)], check=True)
    lib = c.CDLL(str(library))
    lib.tq_threshold_decode.argtypes = [c.POINTER(State), c.c_uint8, c.c_void_p, c.c_size_t]
    lib.tq_threshold_encode_bundle.argtypes = [c.POINTER(State), c.c_uint8, c.c_void_p, c.c_size_t]
    lib.tq_threshold_encode_update.argtypes = [c.POINTER(State), c.c_int, c.c_uint8, c.c_uint16, c.c_void_p]
    lib.tq_threshold_internal_value.argtypes = [c.c_int, c.c_uint16, c.POINTER(c.c_uint32)]
    return lib


def decode(lib, state, command, data):
    raw = c.create_string_buffer(data)
    return lib.tq_threshold_decode(c.byref(state), command, raw, len(data))


def encode(lib, state, command, capacity=12):
    raw = (c.c_ubyte * capacity)()
    length = lib.tq_threshold_encode_bundle(c.byref(state), command, raw, capacity)
    return length, bytes(raw[:length]) if length > 0 else b""


E6 = bytes.fromhex("05 01 00 64 07 01 00 0D")
E7 = bytes.fromhex("01 01 00 19 03 01 01 09 04 01 00 4B")


def populated(lib):
    state = State()
    assert decode(lib, state, 0xE6, E6) == 0
    assert decode(lib, state, 0xE7, E7) == 0
    return state


def test_complete_wire_bundles_are_deterministic(codec):
    state = populated(codec)
    assert encode(codec, state, 0xE6) == (8, E6)
    assert encode(codec, state, 0xE7) == (12, E7)
    assert [x.known for x in state.channel] == [1] * 5


def test_partial_updates_preserve_known_companions(codec):
    state = populated(codec)
    assert decode(codec, state, 0xE7, bytes.fromhex("03 01 01 04")) == 0
    assert state.channel[3].threshold == 260
    assert state.channel[2].threshold == 25
    assert encode(codec, state, 0xE7)[1] == bytes.fromhex(
        "01 01 00 19 03 01 01 04 04 01 00 4B")


def test_big_endian_and_internal_unit_scaling(codec):
    state = populated(codec)
    assert state.channel[3].threshold == 265
    for channel, wire, expected in [(0, 100, 10000), (1, 13, 13000),
                                    (2, 65, 65000), (3, 265, 26500),
                                    (4, 75, 7500)]:
        value = c.c_uint32()
        assert codec.tq_threshold_internal_value(channel, wire, c.byref(value)) == 0
        assert value.value == expected


@pytest.mark.parametrize("cmd,records", [
    (0xE7, bytes.fromhex("01 01 00 19 01 01 00 14")),  # duplicate
    (0xE7, bytes.fromhex("05 01 00 64")),  # wrong bundle
    (0xE6, bytes.fromhex("05 02 00 64")),  # invalid enabled
    (0xE6, bytes.fromhex("05 01 00 27")),  # below UI minimum
    (0xE7, bytes.fromhex("03 01 01 0A")),  # above UI maximum
    (0xE7, bytes.fromhex("FF 01 00 01")),  # unknown selector
    (0xE7, b"\x01\x01\x00"),  # incomplete record
    (0xE6, b""),  # empty
    (0xE5, bytes.fromhex("05 01 00 64")),  # not an observed command
])
def test_rejects_invalid_input_atomically(codec, cmd, records):
    state = populated(codec)
    previous = bytes(state)
    assert decode(codec, state, cmd, records) == -1
    assert bytes(state) == previous


def test_missing_companions_never_emit_guessed_protection_settings(codec):
    state = State()
    output = (c.c_ubyte * 4)()
    assert codec.tq_threshold_encode_update(c.byref(state), 2, 1, 25, output) == -1
    assert encode(codec, state, 0xE7)[0] == -1
    assert decode(codec, state, 0xE7, bytes.fromhex("01 01 00 19")) == 0
    assert codec.tq_threshold_encode_update(c.byref(state), 2, 1, 25, output) == 0xE7
    assert bytes(output) == bytes.fromhex("01 01 00 19")
    assert encode(codec, state, 0xE7)[0] == -1  # remaining fields unknown


def test_update_rejects_out_of_range_and_preserves_destination(codec):
    state = populated(codec)
    output = (c.c_ubyte * 4)(9, 9, 9, 9)
    assert codec.tq_threshold_encode_update(c.byref(state), 3, 1, 266, output) == -1
    assert bytes(output) == b"\x09" * 4
    assert codec.tq_threshold_encode_update(c.byref(state), 3, 0, 265, output) == 0xE7
    assert bytes(output) == bytes.fromhex("03 00 01 09")
    assert encode(codec, state, 0xE7)[1] == E7  # encode is side-effect-free


def test_bundle_never_partially_writes_on_failure(codec):
    state = State()
    output = (c.c_ubyte * 12)(*([0xAA] * 12))
    assert codec.tq_threshold_encode_bundle(c.byref(state), 0xE7, output, 12) == -1
    assert bytes(output) == b"\xAA" * 12
    state = populated(codec)
    assert codec.tq_threshold_encode_bundle(c.byref(state), 0xE7, output, 11) == -1
    assert bytes(output) == b"\xAA" * 12
