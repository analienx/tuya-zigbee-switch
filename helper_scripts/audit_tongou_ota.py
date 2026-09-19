#!/usr/bin/env python3
"""Offline, read-only OTA/GBL metadata audit; NOT a flash or signature verifier.

This tool never rewrites an input, creates an update image, talks to a device,
or treats an outer Zigbee OTA identity as proof of stock bootloader acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

OTA_MAGIC = 0x0BEEF11E
OTA_FIXED_HEADER = struct.Struct('<IHHHHHIH32sI')
GBL3_MAGIC = bytes.fromhex('eb17a603')
MAX_INPUT_SIZE = 32 * 1024 * 1024


class InvalidImage(ValueError):
    """Invalid OTA framing: never offer this input to an updater."""


def _entry_tags(blob: bytes, start: int) -> list[dict[str, object]]:
    tags: list[dict[str, object]] = []
    while start < len(blob):
        if len(blob) - start < 6:
            raise InvalidImage('truncated Zigbee OTA subelement header')
        tag, length = struct.unpack_from('<HI', blob, start)
        start += 6
        if length > len(blob) - start:
            raise InvalidImage('Zigbee OTA subelement exceeds image bounds')
        payload = blob[start:start + length]
        tags.append({
            'id': tag, 'length': length,
            'nested_gbl_magic': tag == 0 and payload.startswith(GBL3_MAGIC),
        })
        start += length
    if not tags or not any(entry['id'] == 0 for entry in tags):
        raise InvalidImage('Zigbee OTA image has no upgrade-image subelement')
    return tags


def audit(blob: bytes) -> dict[str, object]:
    if not blob or len(blob) > MAX_INPUT_SIZE:
        raise InvalidImage('empty input or input exceeds offline analysis limit')
    result: dict[str, object] = {
        'length': len(blob), 'sha256': hashlib.sha256(blob).hexdigest(),
        'signature_verified': False, 'stock_bootloader_acceptance': 'unknown',
    }
    if blob.startswith(GBL3_MAGIC):
        if len(blob) < 16:
            raise InvalidImage('truncated raw GBL3 header')
        return {**result, 'format': 'raw-gbl3',
                'note': 'Use Simplicity Commander for actual GBL tags/signature checks'}
    if len(blob) < OTA_FIXED_HEADER.size:
        raise InvalidImage('file too short for Zigbee OTA header')
    (magic, version, header_len, flags, mfg, image_type, file_version,
     stack_version, label, declared_length) = OTA_FIXED_HEADER.unpack_from(blob)
    if magic != OTA_MAGIC or version != 0x0100 or flags & ~0x7:
        raise InvalidImage('invalid Zigbee OTA magic, header version or flags')
    expected_header_len = 56 + (1 if flags & 1 else 0) + (8 if flags & 2 else 0) + (4 if flags & 4 else 0)
    if header_len != expected_header_len or declared_length != len(blob):
        raise InvalidImage('invalid OTA header size or declared image length')
    cursor = 56
    hardware_min = hardware_max = None
    if flags & 1:
        cursor += 1  # security-credential version
    if flags & 2:
        cursor += 8  # destination IEEE; do not expose in report
    if flags & 4:
        hardware_min, hardware_max = struct.unpack_from('<HH', blob, cursor)
        if hardware_min > hardware_max:
            raise InvalidImage('invalid hardware-version range')
    return {**result, 'format': 'zigbee-ota', 'manufacturer_code': mfg,
            'image_type': image_type, 'file_version': file_version,
            'stack_version': stack_version, 'header_length': header_len,
            'hardware_min': hardware_min, 'hardware_max': hardware_max,
            'subelements': _entry_tags(blob, header_len),
            'note': 'These fields cannot establish device/bootloader acceptance'}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('candidate', type=Path)
    args = ap.parse_args()
    try:
        if args.candidate.stat().st_size > MAX_INPUT_SIZE:
            raise InvalidImage('input exceeds offline analysis limit')
        blob = args.candidate.read_bytes()
        result = audit(blob)
    except (InvalidImage, OSError) as exc:
        # Do not print an input path: it can include private file names.
        print(json.dumps({'valid': False, 'error': type(exc).__name__,
                          'detail': str(exc) if isinstance(exc, InvalidImage)
                          else 'unable to read candidate'}))
        return 2
    print(json.dumps({'framing_checked': result['format'] == 'zigbee-ota',
                      'flash_approved': False, **result}, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
