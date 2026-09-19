import copy
import hashlib
import json
import struct
from pathlib import Path

from helper_scripts.ts0505b.preflash import (
    FAMILY, inspect_candidate, readiness_blockers,
)


def make_artifact():
    def tag(identifier, payload):
        return struct.pack("<II", identifier, len(payload)) + payload

    app = struct.pack("<III", 1, 1, 0) + bytes(16)
    gbl = b"".join([
        tag(0x03A617EB, struct.pack("<II", 0x03000000, 0)),
        tag(0xF40A0AF4, app),
        tag(0xFD0303FD, struct.pack("<I", 0x4000) + b"APP"),
        tag(0xFD0303FD, struct.pack("<I", 0x5000) + b"DATA"),
        tag(0xFC0404FC, struct.pack("<I", 0)),
    ])
    total = 56 + 6 + len(gbl)
    header = struct.pack("<IHHHHHIH", 0x0BEEF11E, 0x0100, 56, 0,
                         0x100B, 0x020C, 0x10003608, 2)
    header += b"TS0505B D0 NOLED".ljust(32, b"\0") + struct.pack("<I", total)
    ota = header + struct.pack("<HI", 0, len(gbl)) + gbl
    frozen = {
        "outer_ota": {"manufacturer_code": "0x100B", "image_type": "0x020C",
                      "file_version": "0x10003608", "total_image_size": len(ota)},
        "artifact_hashes": {"ota_sha256": hashlib.sha256(ota).hexdigest(),
                            "gbl_sha256": hashlib.sha256(gbl).hexdigest()},
        "gbl": {"application_version": 1, "signed": False, "encrypted": False},
        "program_ranges": [{"start": "0x4000", "end_exclusive": "0x4003"},
                           {"start": "0x5000", "end_exclusive": "0x5004"}],
    }
    target = {"stock": {"ota_query": {"manufacturer_code": "0x100B",
                                       "image_type": "0x020C", "file_version": "0x10003607"}}}
    return ota, frozen, target


def test_exact_structural_artifact_passes_offline():
    blob, frozen, target = make_artifact()
    assert inspect_candidate(blob, frozen, target) == []

def test_changed_payload_or_version_is_rejected():
    blob, frozen, target = make_artifact()
    changed = bytearray(blob)
    changed[-16] ^= 0x01
    assert any("SHA-256" in item for item in inspect_candidate(bytes(changed), frozen, target))
    changed = bytearray(blob)
    struct.pack_into("<I", changed, 14, 0x10003607)
    assert any("version" in item for item in inspect_candidate(bytes(changed), frozen, target))


def test_wrong_target_and_staging_envelope_are_rejected():
    blob, frozen, target = make_artifact()
    wrong = copy.deepcopy(target)
    wrong["stock"]["ota_query"]["image_type"] = "0x1602"
    assert any("image_type" in item for item in inspect_candidate(blob, frozen, wrong))
    unsafe = copy.deepcopy(frozen)
    unsafe["program_ranges"][1]["end_exclusive"] = "0xAC001"
    assert any("program ranges" in item for item in inspect_candidate(blob, unsafe, target))


def test_release_fails_closed_without_independent_proof():
    frozen = json.loads((FAMILY / "d0_transport_candidate.json").read_text())
    target = json.loads((FAMILY / "target_manifest.json").read_text())
    board = json.loads((FAMILY / "board_profile.reference.json").read_text())
    proof = json.loads((FAMILY / "preflash_evidence.json").read_text())
    blockers = readiness_blockers(target, frozen, board, proof, artifact_errors=None)
    assert any("not supplied" in item for item in blockers)
    assert any("bootloader" in item for item in blockers)
    assert any("physical RGB+CCT" in item for item in blockers)
    assert any("app version 1" in item for item in blockers)
    assert any("pre-byte" in item for item in blockers)


def test_cli_cannot_claim_ready_without_independent_evidence():
    import subprocess
    import sys

    run = subprocess.run([sys.executable, "-m", "helper_scripts.ts0505b.preflash",
                          "--require-ready"], cwd=FAMILY.parents[2],
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 1
    report = json.loads(run.stdout)
    assert report["status"] == "NOT_READY"
    assert report["artifact_verified"] is False
    assert any("bootloader" in blocker for blocker in report["blockers"])
