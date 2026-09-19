#!/usr/bin/env python3
"""Parse the standard Zigbee OTA image header without modifying any device."""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

OTA_MAGIC = 0x0BEEF11E
BASE_HEADER_LEN = 56


def parse_ota_header(blob: bytes) -> dict:
    if len(blob) < BASE_HEADER_LEN:
        raise ValueError(f"file too small for Zigbee OTA header: {len(blob)} bytes")

    (
        magic,
        header_version,
        header_length,
        field_control,
        manufacturer_code,
        image_type,
        file_version,
        stack_version,
    ) = struct.unpack_from("<IHHHHHIH", blob, 0)

    if magic != OTA_MAGIC:
        raise ValueError(f"bad OTA magic 0x{magic:08X}; expected 0x{OTA_MAGIC:08X}")
    if header_length < BASE_HEADER_LEN:
        raise ValueError(f"invalid OTA header length {header_length}")
    if len(blob) < header_length:
        raise ValueError("file shorter than declared OTA header length")

    header_string_raw = blob[20:52]
    header_string = header_string_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").rstrip()
    total_image_size = struct.unpack_from("<I", blob, 52)[0]

    offset = BASE_HEADER_LEN
    result = {
        "magic": f"0x{magic:08X}",
        "header_version": header_version,
        "header_length": header_length,
        "field_control": field_control,
        "manufacturer_code": manufacturer_code,
        "manufacturer_code_hex": f"0x{manufacturer_code:04X}",
        "image_type": image_type,
        "image_type_hex": f"0x{image_type:04X}",
        "file_version": file_version,
        "file_version_hex": f"0x{file_version:08X}",
        "stack_version": stack_version,
        "header_string": header_string,
        "total_image_size": total_image_size,
        "actual_file_size": len(blob),
    }

    # ZCL OTA field-control optional fields, in standard order.
    if field_control & 0x0001:
        if offset + 1 > header_length:
            raise ValueError("security credential field exceeds header")
        result["security_credential_version"] = blob[offset]
        offset += 1

    if field_control & 0x0002:
        if offset + 8 > header_length:
            raise ValueError("upgrade destination field exceeds header")
        destination = blob[offset : offset + 8]
        result["upgrade_file_destination"] = destination.hex()
        offset += 8

    if field_control & 0x0004:
        if offset + 4 > header_length:
            raise ValueError("hardware-version fields exceed header")
        minimum, maximum = struct.unpack_from("<HH", blob, offset)
        result["minimum_hardware_version"] = minimum
        result["maximum_hardware_version"] = maximum
        offset += 4

    result["declared_size_matches_file"] = total_image_size == len(blob)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--expect-manufacturer", type=lambda x: int(x, 0))
    parser.add_argument("--expect-image-type", type=lambda x: int(x, 0))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = parse_ota_header(args.file.read_bytes())
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for key, value in data.items():
            print(f"{key}: {value}")

    if args.expect_manufacturer is not None and data["manufacturer_code"] != args.expect_manufacturer:
        raise SystemExit(
            f"manufacturer mismatch: 0x{data['manufacturer_code']:04X} != 0x{args.expect_manufacturer:04X}"
        )
    if args.expect_image_type is not None and data["image_type"] != args.expect_image_type:
        raise SystemExit(f"image type mismatch: 0x{data['image_type']:04X} != 0x{args.expect_image_type:04X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
