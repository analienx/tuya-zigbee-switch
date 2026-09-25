"""Offline identity-gate tests; synthetic images only, never live bytes."""
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_ota_identity import (IdentityError, check_index, emit_make_vars,
                                gate_image, load_registry, suggest_next)


def make_image(image_type, file_version, *strings, size=256):
    header = struct.pack('<IHHH', 0x0BEEF11E, 256, 56, 0)
    header += struct.pack('<HHI', 4417, image_type, file_version)
    header += struct.pack('<H', 2) + b'Telink OTA Image'.ljust(32, b'\x00')
    header += struct.pack('<I', size)
    blob = bytearray(header)
    for text in strings:
        blob += text.encode() + b'\x00'
    return bytes(blob.ljust(size, b'\x00'))


def registry(tmp_path):
    data = {"lines": [
        {"board": "test client", "board_key": "testboard",
         "image_type": 65026, "role": "client",
         "versions": [
             {"file_version": 100, "version_str": "1.1.2-bseedcli4",
              "sha512": None, "status": "canary"},
             {"file_version": 101, "version_str": "1.1.2-bseedcli5",
              "sha512": "deadbeef", "status": "canary"}]},
        {"board": "test router", "board_key": "testboard",
         "image_type": 43555, "role": "router",
         "versions": [
             {"file_version": 105, "version_str": "1.1.3-bseedv8",
              "sha512": None, "status": "reserved"}]},
        {"board": "wrappers", "image_type": 54179, "role": "wrapper",
         "shared_identity": True, "versions": [
             {"file_version": 4294967295, "sha512": "wrapper1",
              "status": "released"}]},
    ]}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))
    return load_registry(path)


def test_gate_accepts_new_version_with_matching_string(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 106, "1.1.2-bseedcli6")
    report = gate_image(blob, reg, expect_version_str="1.1.2-bseedcli6",
                        expect_image_type=65026, expect_file_version=106)
    assert report["verdict"] == "ok-new-version"
    assert report["file_version"] == 106


def test_gate_refuses_relabel_and_stale_string(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 101, "1.1.2-bseedcli4")
    with pytest.raises(IdentityError, match="RELABEL"):
        gate_image(blob, reg, expect_version_str="1.1.2-bseedcli5")


def test_gate_refuses_missing_and_stray_strings(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 106, "1.1.2-bseedcli4")
    with pytest.raises(IdentityError, match="not embedded"):
        gate_image(blob, reg, expect_version_str="1.1.2-bseedcli6")
    blob = make_image(65026, 106, "1.1.2-bseedcli6", "1.1.2-bseedcli4")
    with pytest.raises(IdentityError, match="stray version strings"):
        gate_image(blob, reg, expect_version_str="1.1.2-bseedcli6")


def test_gate_refuses_downgrade_unless_allowed(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 100, "1.1.2-bseedcli4")
    with pytest.raises(IdentityError, match="below line maximum"):
        gate_image(blob, reg)
    report = gate_image(blob, reg, allow_downgrade=True)
    assert report["verdict"] == "ok-downgrade-allowed"


def test_gate_rejects_corrupt_and_unknown_lines(tmp_path):
    reg = registry(tmp_path)
    with pytest.raises(IdentityError, match="Truncated"):
        gate_image(b"short", reg)
    blob = make_image(9999, 1, "1.1.2-bseedx")
    with pytest.raises(IdentityError, match="no registry line"):
        gate_image(blob, reg)


def test_identical_rebuild_still_checks_registry_string(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 101, "1.1.2-bseedcli5")
    reg[65026]["versions"][1]["sha512"] = hashlib.sha512(blob).hexdigest()
    report = gate_image(blob, reg)
    assert report["verdict"] == "ok-identical-rebuild"
    reg[65026]["versions"][1]["version_str"] = "1.1.2-bseedcliX"
    with pytest.raises(IdentityError, match="not embedded"):
        gate_image(blob, reg)


def test_check_index_gates_a_directory(tmp_path):
    reg = registry(tmp_path)
    ota_dir = tmp_path / "ota"
    ota_dir.mkdir()
    (ota_dir / "good.ota").write_bytes(
        make_image(65026, 106, "1.1.2-bseedcli6"))
    assert check_index(reg, ota_dir) == 1
    (ota_dir / "bad.ota").write_bytes(
        make_image(65026, 101, "1.1.2-bseedcli4"))
    with pytest.raises(IdentityError, match="refused"):
        check_index(reg, ota_dir)


def test_board_maximum_spans_both_roles(tmp_path):
    reg = registry(tmp_path)
    blob = make_image(65026, 103, "1.1.2-bseedcli6")
    with pytest.raises(IdentityError, match="board maximum"):
        gate_image(blob, reg, expect_version_str="1.1.2-bseedcli6")


def test_suggest_next_and_emit_make_vars(tmp_path):
    reg = registry(tmp_path)
    nxt = suggest_next(reg, 65026)
    assert nxt["next_file_version"] == 106
    assert nxt["next_file_version_hex"] == "0x6a"
    out = emit_make_vars(reg, 65026, "1.1.2-bseedcli6")
    assert out == {"FILE_VERSION": "0x6a", "VERSION_STR": "1.1.2-bseedcli6"}
    with pytest.raises(IdentityError, match="already used"):
        emit_make_vars(reg, 65026, "1.1.2-bseedcli4")
    with pytest.raises(IdentityError, match="shared-identity"):
        suggest_next(reg, 54179)
