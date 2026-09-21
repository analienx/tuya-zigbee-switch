"""Offline image-header gate rejects tampering and enumerates each role."""
import struct

import pytest
from helper_scripts.bseed_antibrick_rc_gate import CASES, ota_header


def _header(image_type=43556, version=302329870, size=60, manufacturer=4417):
    return struct.pack('<I5HIH32sI', 0x0BEEF11E, 0x0100, 56, 0,
                       manufacturer, image_type, version, 2, b'BSEED', size) + b'1234'


def test_role_board_matrix_and_distinct_ota_tuple():
    assert len(CASES) == 4
    assert len({(row[6], row[5]) for row in CASES}) == 4
    assert {row[3] for row in CASES} == {'Router', 'EndDevice'}
    assert all(row[5] > row[7] for row in CASES)


def test_exact_header_and_corrupt_images():
    assert ota_header(_header()) == (4417, 43556, 302329870)
    for image in (_header()[:-1], _header(manufacturer=1), b'',
                  _header(size=0), _header()[:20]):
        with pytest.raises((ValueError, AssertionError)):
            ota_header(image)
