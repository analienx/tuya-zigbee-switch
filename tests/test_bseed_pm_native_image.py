import binascii
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_pm_release import CANDIDATES, CLIENT, verify_native_image, validate_candidate_set
from bseed_ota_identity import IdentityError
from tests.bseed_image_fixture import image_for


@pytest.mark.parametrize('candidate', list(CANDIDATES.values()))
def test_native_identity_and_basic_string_verified(candidate):
    result = verify_native_image(image_for(candidate), candidate)
    assert result['nativeIntegrityVerified'] and result['basicBuildIdVerified']
    assert not result['hardwareAcceptance']


@pytest.mark.parametrize('offset,reason', [(4, 'header format'), (8, 'header format'), (18, 'header format'),
    (12, 'transport'), (14, 'transport'),
    (56, 'subelement'), (62+8, 'marker'), (62+2, 'embedded version'),
    (62+24, 'length'), (62+100, 'CRC')])
def test_header_native_and_crc_corruption_rejected(offset, reason):
    data = bytearray(image_for(CLIENT))
    data[offset] ^= 1
    with pytest.raises(IdentityError, match=reason):
        verify_native_image(bytes(data), CLIENT)


def test_correct_crc_does_not_mask_wrong_basic_string_length():
    data = bytearray(image_for(CLIENT))
    data[62+39] -= 1
    struct.pack_into('<I', data, len(data)-4, binascii.crc32(data[62:-4]) ^ 0xffffffff)
    with pytest.raises(IdentityError, match='length-prefixed'):
        verify_native_image(bytes(data), CLIENT)


def test_candidate_set_rejects_equal_version_return(monkeypatch):
    monkeypatch.setitem(CANDIDATES['return'], 'version', CLIENT['version'])
    with pytest.raises(IdentityError, match='return identity'):
        validate_candidate_set()


def test_candidate_set_rejects_overlength_basic_name(monkeypatch):
    monkeypatch.setitem(CLIENT, 'build', '1.2.5-bseedv8u5-rc5')
    with pytest.raises(IdentityError, match='16 ASCII'):
        validate_candidate_set()
