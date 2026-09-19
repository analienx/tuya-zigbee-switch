#!/usr/bin/env python3
"""Offline TS0505B artifact inspection and fail-closed deployment readiness gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from .parse_gbl import parse_gbl
from .parse_zigbee_ota import parse_ota_header

ROOT = Path(__file__).resolve().parents[2]
FAMILY = ROOT / "devices" / "ts0505b-mja6r5ix" / "firmware"
STOCK = 0x10003607
ALLOWED_TAGS = {"header", "application_info", "erase_program_data", "program_data", "end"}


def as_int(value: int | str) -> int:
    return int(value, 0) if isinstance(value, str) else value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def inspect_candidate(blob: bytes, frozen: dict, target: dict) -> list[str]:
    """Reject any byte deviation or unsafe GBL write envelope; no device access."""
    errors: list[str] = []
    ota_spec, hashes = frozen["outer_ota"], frozen["artifact_hashes"]
    expected_size = ota_spec["total_image_size"]
    if len(blob) != expected_size or digest(blob) != hashes["ota_sha256"]:
        errors.append("OTA length or SHA-256 differs from frozen artifact")
    try:
        ota = parse_ota_header(blob)
    except (ValueError, struct.error) as exc:
        return errors + [f"OTA header: {exc}"]
    stock = target["stock"]["ota_query"]
    for key in ("manufacturer_code", "image_type"):
        value = as_int(ota_spec[key])
        if ota[key] != value or value != as_int(stock[key]):
            errors.append(f"{key} differs from exact stock OTA identity")
    if ota["file_version"] != as_int(ota_spec["file_version"]):
        errors.append("outer OTA version differs from frozen candidate")
    if ota["file_version"] <= as_int(stock["file_version"]):
        errors.append("outer OTA version does not advance stock version")
    if ota["field_control"] != 0 or ota["header_version"] != 0x0100:
        errors.append("unsupported OTA field control/header version")
    if not ota["declared_size_matches_file"] or ota["header_length"] != 56:
        errors.append("OTA size or header length inconsistent")
    start = ota["header_length"]
    if start + 6 > len(blob):
        return errors + ["missing OTA sub-element header"]
    tag, length = struct.unpack_from("<HI", blob, start)
    gbl_start = start + 6
    if tag != 0 or gbl_start + length != len(blob):
        return errors + ["expected exactly one full-image OTA sub-element"]
    gbl_blob = blob[gbl_start:]
    if digest(gbl_blob) != hashes["gbl_sha256"]:
        errors.append("embedded GBL SHA-256 differs from frozen artifact")
    try:
        gbl = parse_gbl(gbl_blob)
    except (ValueError, struct.error) as exc:
        return errors + [f"embedded GBL: {exc}"]
    if not gbl["has_end_tag"] or gbl["tags"][-1]["name"] != "end":
        errors.append("GBL is not properly terminated")
    if [tag["name"] for tag in gbl["tags"]] != [
        "header", "application_info", "erase_program_data", "erase_program_data", "end"
    ]:
        errors.append("GBL contains unexpected tags or upgrade payloads")
    if any(tag["name"] not in ALLOWED_TAGS for tag in gbl["tags"]):
        errors.append("GBL has unknown, compressed, encrypted or security upgrade tags")
    if gbl["has_bootloader_upgrade"] or gbl["has_se_upgrade"]:
        errors.append("GBL attempts bootloader or Secure Engine modification")
    last = gbl["tags"][-1]
    if last["offset"] + 8 + last["length"] != len(gbl_blob):
        errors.append("GBL has trailing bytes after end tag")
    expected_gbl = frozen["gbl"]
    if (gbl["signed_flag"], gbl["encrypted_flag"]) != (
        expected_gbl["signed"], expected_gbl["encrypted"]
    ):
        errors.append("GBL security flags differ from frozen candidate")
    if not gbl["application_info"] or (
        gbl["application_info"]["application_version"] != expected_gbl["application_version"]
    ):
        errors.append("internal application version differs from frozen candidate")
    actual = [(x["flash_start_address"], x.get("flash_end_address_exclusive"))
              for x in gbl["program_ranges"]]
    expected = [(as_int(x["start"]), as_int(x["end_exclusive"]))
                for x in frozen["program_ranges"]]
    if actual != expected:
        errors.append("GBL program ranges differ from frozen candidate")
    for index, (begin, end) in enumerate(actual):
        if end is None or begin < 0x4000 or end <= begin or end > 0xAC000:
            errors.append(f"program range {index} exceeds conservative reference envelope")
        if index and actual[index - 1][1] is not None and begin < actual[index - 1][1]:
            errors.append(f"program range {index} overlaps prior range")
    return errors

def readiness_blockers(target: dict, frozen: dict, board: dict, proof: dict,
                       artifact_errors: list[str] | None) -> list[str]:
    blockers = []
    if artifact_errors is None:
        blockers.append("frozen OTA bytes were not supplied for byte-for-byte verification")
    else:
        blockers += artifact_errors
    if frozen.get("deployment_ready") is not True or target.get("deployment_ready") is not True:
        blockers.append("candidate and target are explicitly marked experimental / not deployable")
    if board.get("deployment_eligible") is True:
        validation = board.get("production_board_validation", {})
        channels = validation.get("channels", {})
        if (validation.get("status") != "independently_verified"
                or not validation.get("evidence")
                or not isinstance(channels, dict)
                or any(not isinstance(channels.get(channel), dict)
                       or channels[channel].get("verified") is not True
                       or not channels[channel].get("driver_path_evidence")
                       or channels[channel].get("safe_reset_verified") is not True
                       for channel in ("red", "green", "blue", "cold_white", "warm_white"))):
            blockers.append("production RGB+CCT channel/driver/reset evidence missing; reference GPIO labels alone do not authorize outputs")
    if board.get("deployment_eligible") is not True:
        blockers.append("physical RGB+CCT board mapping/polarity/PWM/safe startup not verified")
    for key, description in (
        ("installed_platform_and_memory", "installed SoC, flash density and bootloader/storage layout"),
        ("prebyte_ota_acceptance", "stock OTA pre-byte acceptance for exact candidate identity"),
        ("stock_application_properties", "stock internal Gecko Application Properties version"),
        ("bootloader_policy", "stock signature, encryption, rollback and secure-boot policy"),
        ("recoverability", "exact stock rollback or independently verified safe recovery"),
        ("lighting_behavior", "physical RGB+CCT, reset, power and Zigbee behavior"),
    ):
        record = proof.get(key, {})
        if record.get("status") != "independently_verified" or not record.get("evidence"):
            blockers.append(f"unverified: {description}")
    if frozen.get("gbl", {}).get("application_version") == 1 and (
        proof.get("stock_application_properties", {}).get("status") != "independently_verified"
    ):
        blockers.append("candidate internal app version 1 is not proven acceptable to stock bootloader")
    return blockers

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, help="local D0 OTA bytes; never sends firmware")
    parser.add_argument("--require-ready", action="store_true", help="fail unless all release gates pass")
    args = parser.parse_args()
    frozen = json.loads((FAMILY / "d0_transport_candidate.json").read_text(encoding="utf-8"))
    target = json.loads((FAMILY / "target_manifest.json").read_text(encoding="utf-8"))
    board = json.loads((FAMILY / "board_profile.reference.json").read_text(encoding="utf-8"))
    proof = json.loads((FAMILY / "preflash_evidence.json").read_text(encoding="utf-8"))
    errors = inspect_candidate(args.artifact.read_bytes(), frozen, target) if args.artifact else None
    blockers = readiness_blockers(target, frozen, board, proof, errors)
    result = {
        "status": "NOT_READY" if blockers else "READY_FOR_SEPARATELY_AUTHORIZED_CANARY",
        "scope": "offline only; no OTA traffic, device write or deployment authorization",
        "artifact_supplied": args.artifact is not None,
        "artifact_verified": errors == [] if errors is not None else False,
        "blockers": blockers,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(bool(blockers) and args.require_ready) or int(errors is not None and bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
