#!/usr/bin/env python3
"""Parse Silicon Labs GBL v3-style tag structure for offline safety analysis."""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

TAG_NAMES = {
    0x03A617EB: "header",
    0x76A617EB: "version_dependency",
    0xF40A0AF4: "application_info",
    0x5EA617EB: "se_upgrade",
    0xF50909F5: "bootloader",
    0xFE0101FE: "program_data",
    0xFD0303FD: "erase_program_data",
    0xFD0505FD: "program_data_lz4",
    0xFD0707FD: "program_data_lzma",
    0xF80A0AF8: "delta",
    0xF80B0BF8: "delta_lz4",
    0xF80C0CF8: "delta_lzma",
    0xF60808F6: "metadata",
    0xF30B0BF3: "certificate",
    0xF70A0AF7: "signature",
    0xFA0606FA: "encryption_init",
    0xF90707F9: "encrypted_data",
    0xFC0404FC: "end",
}

GBL_HEADER_TAG = 0x03A617EB
GBL_TYPE_ENCRYPTION_AESCCM = 0x00000001
GBL_TYPE_SIGNATURE_ECDSA = 0x00000100


def parse_gbl(blob: bytes, offset: int = 0) -> dict:
    if offset < 0 or offset + 16 > len(blob):
        raise ValueError("not enough bytes for a GBL header")

    first_id, first_len = struct.unpack_from("<II", blob, offset)
    if first_id != GBL_HEADER_TAG:
        raise ValueError(f"bad GBL header tag 0x{first_id:08X}")
    if first_len < 8 or offset + 8 + first_len > len(blob):
        raise ValueError("invalid GBL header length")

    version, gbl_type = struct.unpack_from("<II", blob, offset + 8)
    cursor = offset
    tags: list[dict] = []
    program_ranges: list[dict] = []
    app_info = None

    while cursor + 8 <= len(blob):
        tag_id, length = struct.unpack_from("<II", blob, cursor)
        payload_start = cursor + 8
        payload_end = payload_start + length
        if payload_end > len(blob):
            raise ValueError(f"tag 0x{tag_id:08X} exceeds file at offset {cursor}")

        entry = {
            "offset": cursor,
            "tag_id": tag_id,
            "tag_id_hex": f"0x{tag_id:08X}",
            "name": TAG_NAMES.get(tag_id, "unknown"),
            "length": length,
        }
        tags.append(entry)

        if tag_id == 0xF40A0AF4 and length >= 28:
            app_type, app_version, capabilities = struct.unpack_from("<III", blob, payload_start)
            product_id = blob[payload_start + 12 : payload_start + 28]
            app_info = {
                "application_type": app_type,
                "application_version": app_version,
                "capabilities": capabilities,
                "product_id_hex": product_id.hex(),
            }

        if tag_id in {0xFE0101FE, 0xFD0303FD, 0xFD0505FD, 0xFD0707FD} and length >= 4:
            address = struct.unpack_from("<I", blob, payload_start)[0]
            item = {
                "tag": TAG_NAMES.get(tag_id, "program_data"),
                "flash_start_address": address,
                "flash_start_address_hex": f"0x{address:08X}",
                "encoded_data_length": length - 4,
                "erase_before_program": tag_id == 0xFD0303FD,
                "encoding": {
                    0xFE0101FE: "plain",
                    0xFD0303FD: "plain",
                    0xFD0505FD: "lz4",
                    0xFD0707FD: "lzma",
                }[tag_id],
            }
            if tag_id in {0xFE0101FE, 0xFD0303FD}:
                end = address + length - 4
                item["flash_data_length"] = length - 4
                item["flash_end_address_exclusive"] = end
                item["flash_end_address_exclusive_hex"] = f"0x{end:08X}"
            program_ranges.append(item)

        cursor = payload_end
        if tag_id == 0xFC0404FC:
            break

    return {
        "gbl_offset": offset,
        "gbl_version": version,
        "gbl_version_hex": f"0x{version:08X}",
        "gbl_type": gbl_type,
        "gbl_type_hex": f"0x{gbl_type:08X}",
        "encrypted_flag": bool(gbl_type & GBL_TYPE_ENCRYPTION_AESCCM),
        "signed_flag": bool(gbl_type & GBL_TYPE_SIGNATURE_ECDSA),
        "has_signature_tag": any(tag["tag_id"] == 0xF70A0AF7 for tag in tags),
        "has_certificate_tag": any(tag["tag_id"] == 0xF30B0BF3 for tag in tags),
        "has_encrypted_data": any(tag["tag_id"] == 0xF90707F9 for tag in tags),
        "has_bootloader_upgrade": any(tag["tag_id"] == 0xF50909F5 for tag in tags),
        "has_se_upgrade": any(tag["tag_id"] == 0x5EA617EB for tag in tags),
        "has_end_tag": any(tag["tag_id"] == 0xFC0404FC for tag in tags),
        "application_info": app_info,
        "program_ranges": program_ranges,
        "tags": tags,
    }


def find_gbl(blob: bytes) -> int | None:
    needle = struct.pack("<I", GBL_HEADER_TAG)
    position = blob.find(needle)
    return position if position >= 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--scan", action="store_true", help="scan for an embedded GBL header")
    args = parser.parse_args()

    blob = args.file.read_bytes()
    offset = find_gbl(blob) if args.scan else 0
    if offset is None:
        raise SystemExit("no GBL header tag found")
    print(json.dumps(parse_gbl(blob, offset), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
