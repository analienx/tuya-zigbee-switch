"""Synthetic framing tests: no proprietary image, Zigbee network or flash writes."""
import struct

import pytest

from helper_scripts.audit_tongou_ota import (
    GBL3_MAGIC, InvalidImage, OTA_MAGIC, audit,
)


def ota(payload: bytes = GBL3_MAGIC + b'\x00' * 12,
        *, mfg: int = 0x1002, image: int = 0x1602,
        flags: int = 0, extra: bytes = b'') -> bytes:
    tag = struct.pack('<HI', 0, len(payload)) + payload
    size = 56 + len(extra) + len(tag)
    header = struct.pack('<IHHHHHIH32sI', OTA_MAGIC, 0x0100,
                         56 + len(extra), flags, mfg, image, 75, 2,
                         b'research only'.ljust(32, b'\x00'), size)
    return header + extra + tag


def test_stock_query_tuple_is_not_a_signature_or_acceptance():
    result = audit(ota())
    assert (result['manufacturer_code'], result['image_type'],
            result['file_version']) == (4098, 5634, 75)
    assert result['subelements'][0]['nested_gbl_magic'] is True
    assert result['signature_verified'] is False
    assert result['stock_bootloader_acceptance'] == 'unknown'


def test_header_identifiers_are_generic_and_not_a_board_fingerprint():
    other = audit(ota(mfg=4098, image=5634, payload=b'OTHER DEVICE'))
    assert other['subelements'][0]['nested_gbl_magic'] is False
    assert other['stock_bootloader_acceptance'] == 'unknown'


def test_raw_gbl_is_only_classified_not_verified():
    value = audit(GBL3_MAGIC + b'\xFF' * 12)
    assert value['format'] == 'raw-gbl3'
    assert not value['signature_verified']


@pytest.mark.parametrize('bad', [
    b'', b'\x1E\xF1\xEE\x0B',
    ota()[:-1], ota() + b'\x00',
    ota()[:56] + b'\x00\x00',
    ota().replace(b'\x1e\xf1\xee\x0b', b'ABCD', 1),
    ota(flags=0, extra=b'\x00'),
    ota(flags=4, extra=struct.pack('<HH', 9, 3)),
    ota()[:58] + b'\xff' * 4 + ota()[62:],
])
def test_corrupted_or_truncated_containers_fail_closed(bad):
    with pytest.raises(InvalidImage):
        audit(bad)
