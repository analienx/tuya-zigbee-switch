"""Return package must preserve native integrity and never reuse Client tuples."""
import binascii
import hashlib
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_pm_client_return import RETURN, verify_return
from bseed_ota_identity import IdentityError


def package():
    fw = bytearray(128)
    struct.pack_into('<I', fw, 2, RETURN['version'])
    fw[6:12] = b'\x5d\x02KNLT'
    struct.pack_into('<I', fw, 0x18, len(fw))
    build = RETURN['build'].encode()
    fw[40:40+len(build)] = build
    struct.pack_into('<I', fw, len(fw)-4, binascii.crc32(fw[:-4]) ^ 0xffffffff)
    payload = struct.pack('<HI', 0, len(fw)) + fw
    def image(t):
        return struct.pack('<I5HIH32sI', 0x0beef11e, 0x100, 56, 0, 4417,
                           t, RETURN['version'], 2, b'', 56+len(payload)) + payload
    registry = {t: {'board_key': 'b28wrpvx', 'versions':
                    [{'file_version': 0x12053012, 'sha512': None}]}
                for t in (43556, 65024)}
    return image(43556), image(65024), registry


def test_valid_package_is_not_hardware_or_apply_acceptance():
    native, wrapper, registry = package()
    report = verify_return(native, wrapper, registry)
    assert report['payloadIdentical'] and report['nativeIntegrityVerified']
    assert report['sha256'] == hashlib.sha256(wrapper).hexdigest()
    assert not report['hardwareAcceptance']
    assert not report['applyPathFix']
    assert not report['deploymentReady']


def test_equal_installed_version_is_refused():
    with pytest.raises(ValueError, match='exceed'):
        verify_return(*package(), installed_version=RETURN['version'])


def test_modified_wrapper_payload_is_refused():
    native, wrapper, registry = package()
    with pytest.raises(ValueError, match='payload'):
        verify_return(native, wrapper[:-1]+bytes([wrapper[-1] ^ 1]), registry)


@pytest.mark.parametrize('offset,message', [(56, 'subelement'), (62+8, 'marker'),
                         (62+2, 'embedded version'), (62+0x18, 'size'), (62+90, 'CRC')])
def test_identical_but_invalid_native_payload_is_refused(offset, message):
    native, wrapper, registry = package()
    a, b = bytearray(native), bytearray(wrapper)
    a[offset] ^= 1
    b[offset] ^= 1
    with pytest.raises(ValueError, match=message):
        verify_return(bytes(a), bytes(b), registry)


def test_wrapper_collision_with_a_client_release_is_refused():
    native, wrapper, registry = package()
    registry[65024]['versions'].append({'file_version': RETURN['version'],
                                      'sha512': 'different-client-bytes'})
    with pytest.raises(IdentityError, match='RELABEL REFUSED'):
        verify_return(native, wrapper, registry)
