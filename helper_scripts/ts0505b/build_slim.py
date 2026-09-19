#!/usr/bin/env python3
"""Offline reproducible TS0505B dark-reference build; never deploys to a device."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from .parse_gbl import parse_gbl
from .parse_zigbee_ota import parse_ota_header

FAMILY = Path(__file__).resolve().parents[2] / "devices" / "ts0505b-mja6r5ix" / "firmware"
SEED = FAMILY / "slim_reference"
APP_VERSION = 0x10003608
MAX_RESEARCH_OTA_SIZE = 196608
CHIPS = {768: "EFR32MG21A020F768IM32", 1024: "EFR32MG21A020F1024IM32"}
EXCLUDE = (
    "zigbee_gp", "zigbee_green_power_client", "zigbee_green_power_client_cli",
    "zigbee_green_power_common", "zigbee_zll", "zigbee_zll_commissioning_common",
    "zigbee_zll_commissioning_server", "zigbee_zll_identify_server",
    "zigbee_zll_level_control_server", "zigbee_zll_on_off_server",
    "zigbee_zll_scenes_server", "zigbee_zll_utility_server", "zigbee_zcl_cli",
    "zigbee_core_cli", "zigbee_debug_basic", "zigbee_debug_print",
    "zigbee_stack_diagnostics", "cli", "zigbee_counters", "zigbee_interpan",
    "zigbee_find_and_bind_target",
)


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def pack_ota(gbl: bytes) -> bytes:
    """Standard 56-byte Zigbee header and one type-0 full-image sub-element."""
    name = b"TS0505B SLIM D0 NOLED".ljust(32, b"\x00")
    total = 56 + 6 + len(gbl)
    if total > MAX_RESEARCH_OTA_SIZE:
        raise ValueError(f"OTA exceeds observed pre-byte acceptance size: {total}")
    header = struct.pack("<IHHHHHIH32sI", 0x0BEEF11E, 0x100, 56, 0,
                         0x100B, 0x020C, APP_VERSION, 2, name, total)
    return header + struct.pack("<HI", 0, len(gbl)) + gbl


def inspect_local(ota: bytes) -> dict:
    info = parse_ota_header(ota)
    if not info["declared_size_matches_file"] or info["header_length"] != 56:
        raise ValueError("invalid outer OTA size/header")
    if (info["manufacturer_code"], info["image_type"], info["file_version"]) != (
        0x100B, 0x020C, APP_VERSION,
    ):
        raise ValueError("outer OTA identity mismatch")
    tag, length = struct.unpack_from("<HI", ota, 56)
    if tag != 0 or length != len(ota) - 62:
        raise ValueError("OTA must contain exactly one full-image GBL sub-element")
    parsed = parse_gbl(ota[62:])
    if [t["name"] for t in parsed["tags"]] != [
        "header", "application_info", "erase_program_data", "erase_program_data", "end",
    ] or not parsed["has_end_tag"]:
        raise ValueError("unsupported GBL tag structure")
    if parsed["signed_flag"] or parsed["encrypted_flag"] or (
        parsed["has_bootloader_upgrade"] or parsed["has_se_upgrade"]
    ):
        raise ValueError("unsupported signing/encryption or bootloader/SE upgrade")
    if parsed["application_info"]["application_version"] != APP_VERSION:
        raise ValueError("internal GBL application version mismatch")
    ends = [r["flash_end_address_exclusive"] for r in parsed["program_ranges"]]
    if len(ends) != 2 or any(e is None for e in ends) or max(ends) >= 768 * 1024:
        raise ValueError("application writes exceed conservative reference flash envelope")
    if any(r["flash_start_address"] < 0x4000 for r in parsed["program_ranges"]):
        raise ValueError("application writes overlap bootloader start area")
    return {"app_version": APP_VERSION, "highest_write_end": max(ends),
            "program_ranges": parsed["program_ranges"]}


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("slc", "sdk", "cmake", "commander", "ninja_dir", "out"):
        parser.add_argument("--" + flag.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--chip-kib", type=int, choices=sorted(CHIPS), default=768)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--out must be a new or empty directory; existing builds are never overwritten")
    if not all(Path(getattr(args, x)).exists() for x in
               ("slc", "sdk", "cmake", "commander", "ninja_dir")):
        parser.error("one or more tool/SDK paths do not exist")
    shutil.copytree(SEED, output, dirs_exist_ok=True)
    for filename in ("ts0505b_light_state.c", "ts0505b_light_state.h",
                     "ts0505b_zcl_adapter.c", "ts0505b_zcl_adapter.h"):
        shutil.copyfile(FAMILY / filename, output / filename)
    project = output / "ts0505b_slim_reference.slcp"
    spec = project.read_text(encoding="utf-8")
    present = [name for name in EXCLUDE if f"  id: {name}\n" in spec]
    if present:
        raise ValueError(f"SLCP still contains optional large components: {present}")
    for chip in CHIPS.values():
        spec = spec.replace(f"  id: {chip}", f"  id: {CHIPS[args.chip_kib]}")
    project.write_text(spec, encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(args.ninja_dir.resolve()) + os.pathsep + env.get("PATH", "")
    run([str(args.slc), "generate", "--project-file", str(project),
         "--sdk", str(args.sdk), "--destination", str(output),
         "--output-type", "cmake"], output, env)
    cmake_dir = output / "cmake_gcc"
    run([str(args.cmake), "--preset", "project"], cmake_dir, env)
    run([str(args.cmake), "--build", "--preset", "default_config"], cmake_dir, env)
    built = cmake_dir / "build" / "base" / "ts0505b_slim_reference"
    application = built.with_suffix(".s37")
    binary = built.with_suffix(".bin")
    gbl_path = output / "ts0505b_slim_reference.gbl"
    ota_path = output / "ts0505b_slim_reference.ota"
    run([str(args.commander), "gbl", "create", str(gbl_path),
         "--app", str(application)], output, env)
    gbl = gbl_path.read_bytes()
    ota = pack_ota(gbl)
    proof = inspect_local(ota)
    ota_path.write_bytes(ota)
    result = {
        "status": "OFFLINE_FORMAT_PASS_NOT_DEPLOYABLE",
        "chip_template": CHIPS[args.chip_kib],
        "app_version": f"0x{APP_VERSION:08X}",
        "application_binary_bytes": binary.stat().st_size,
        "application_binary_sha256": sha(binary.read_bytes()),
        "gbl_bytes": len(gbl), "gbl_sha256": sha(gbl),
        "ota_bytes": len(ota), "ota_sha256": sha(ota),
        "highest_written_address_exclusive": proof["highest_write_end"],
        "physical_board_mapping_verified": False,
        "installed_bootloader_acceptance_verified": False,
        "recovery_path_verified": False,
        "live_flash_authorized": False,
    }
    (output / "build_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
