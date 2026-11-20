#!/usr/bin/env python3
"""Fail-closed validation of a hosted ZG-204ZL v0.1.4.3 build output.

This does not authorize a physical firmware write or OTA deployment.
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct
import re

VERSION = 0x01433001
MFG = 0x1141
IMAGE_TYPE = 0x0232
MAX_APP_FOOTPRINT = 0x20000  # never expand the v0.1.4.0 SWire erase footprint


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def verify(binary: Path, ota: Path) -> dict:
    firmware = binary.read_bytes()
    package = ota.read_bytes()
    if len(firmware) < 4096 or len(firmware) > MAX_APP_FOOTPRINT:
        raise ValueError(f"image size {len(firmware)} exceeds allowed v0.1.4.0 flash footprint")
    if firmware[6:8] != b"\x5d\x02" or firmware[8:12] != b"KNLT":
        raise ValueError("missing Telink 8258 magic or active boot marker")
    native_version = int.from_bytes(firmware[2:6], "little")
    native_mfg = int.from_bytes(firmware[18:20], "little")
    native_type = int.from_bytes(firmware[20:22], "little")
    claimed_length = int.from_bytes(firmware[0x18:0x1c], "little")
    if (native_version, native_mfg, native_type) != (VERSION, MFG, IMAGE_TYPE):
        raise ValueError("wrong native fileVersion/manufacturer/imageType")
    if claimed_length != len(firmware):
        raise ValueError(f"wrong native length: {claimed_length} vs {len(firmware)}")
    crc = binascii.crc32(firmware[:-4]) ^ 0xffffffff
    stored_crc = int.from_bytes(firmware[-4:], "little")
    if crc != stored_crc:
        raise ValueError(f"native CRC mismatch: expected {crc:08x}, found {stored_crc:08x}")

    if len(package) != len(firmware) + 62:
        raise ValueError("OTA must contain exactly 56-byte header + 6-byte subelement + firmware")
    header = struct.unpack_from("<I5HIH32sI", package)
    magic, header_ver, header_len, field_control, mfg, image_type, version, stack_ver, title, total_len = header
    if (magic, header_ver, header_len, field_control) != (0x0BEEF11E, 0x0100, 56, 0):
        raise ValueError("wrong OTA magic/size/format")
    if (mfg, image_type, version) != (MFG, IMAGE_TYPE, VERSION):
        raise ValueError("wrong OTA image identity")
    if total_len != len(package) or package[56:62] != struct.pack("<HI", 0, len(firmware)):
        raise ValueError("wrong OTA image/sublength")
    if package[62:] != firmware:
        raise ValueError("OTA payload differs from native image")
    return {
        "status": "HOSTED_BUILD_IMAGE_VERIFIED_NOT_HARDWARE_APPROVED",
        "board": "ZG-204ZL-3.0",
        "chip": "TLSR8258F512ET32",
        "release": "ZG204ZL v0.1.4.3 +6.14 dBm battery-first canary",
        "manufacturer_code": f"0x{MFG:04x}",
        "image_type": f"0x{IMAGE_TYPE:04x}",
        "file_version": f"0x{VERSION:08x}",
        "native": {"file": binary.name, "bytes": len(firmware), "sha256": sha256(firmware)},
        "ota": {"file": ota.name, "bytes": len(package), "sha256": sha256(package)},
        "preserved_region_claim": "Not proven: physical post-write NVM preservation and runtime Zigbee rejoin must be independently measured",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bin", type=Path, required=True)
    p.add_argument("--ota", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    args = p.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
        p.error('--source-commit must be the exact candidate SHA')
    result = verify(args.bin, args.ota)
    result['sourceCommit'] = args.source_commit
    result['upstreamCommit'] = 'c249d8a7caddb5012d06aba580d4a53d1957f9c1'
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
