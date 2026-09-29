"""Offline fail-closed checks of a native non-PM TLSR8258 OTA image."""
import binascii
import hashlib
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'helper_scripts'))
from bseed_targeted_z2m_ota import validate_nonpm_native_ota_image, verify_image
VERSION=285356048

def image():
    native=bytearray(64)
    struct.pack_into('<I',native,2,VERSION)
    native[6:8]=b'\x5d\x02'
    struct.pack_into('<I',native,8,0x544c4e4b)
    struct.pack_into('<I',native,0x18,len(native))
    struct.pack_into('<I',native,len(native)-4,binascii.crc32(native[:-4]) ^ 0xffffffff)
    header=struct.pack('<I5HIH32sI',0x0BEEF11E,256,56,0,4417,65026,VERSION,2,b'Telink OTA Image'.ljust(32,b'\x00'),62+len(native))
    return header+struct.pack('<HI',0,len(native))+bytes(native)

def test_valid_native_ota():
    assert validate_nonpm_native_ota_image(image(),VERSION)

def test_corrupted_inner_crc_fails_even_when_outer_sha_would_be_updated():
    bad=bytearray(image());bad[-8]^=1
    with pytest.raises(AssertionError,match='CRC'): validate_nonpm_native_ota_image(bad,VERSION)

def test_sub_element_and_native_layout_fail_closed():
    original=image()
    cases=[(56,b'\x01','sub-element'),(58,b'\x00','sub-element'),
           (62+6,b'\x00','magic'),(62+8,b'\x00','startup flag'),
           (62+2,b'\x00','embedded version'),(62+0x18,b'\x01','firmware length')]
    for off,value,reason in cases:
        bad=bytearray(original);bad[off:off+len(value)]=value
        with pytest.raises(AssertionError,match=reason):
            validate_nonpm_native_ota_image(bad,VERSION)
    with pytest.raises(AssertionError,match='embedded version'):
        validate_nonpm_native_ota_image(original,VERSION+1)

def test_native_check_is_mandatory_for_nonpm_runner(tmp_path):
    original=image();p=tmp_path/'image.ota';p.write_bytes(original)
    a=SimpleNamespace(image=str(p),native_image=None,non_pm=True,
        sha256=hashlib.sha256(original).hexdigest(),manufacturer_code=4417,
        image_type=65026,file_version=VERSION,url='http://example.invalid/firmware')
    class Http:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def read(self):return p.read_bytes()
    with patch('bseed_targeted_z2m_ota.urllib.request.urlopen',return_value=Http()):
        verify_image(a)
        bad=bytearray(original);bad[-8]^=1;p.write_bytes(bad)
        a.sha256=hashlib.sha256(bad).hexdigest()
        with pytest.raises(AssertionError,match='CRC'):
            verify_image(a)
