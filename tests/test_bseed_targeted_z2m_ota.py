"""Offline regression tests; never connects to MQTT or flashes a device."""
import hashlib
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_targeted_z2m_ota import verify_image


def fixture(tmp_path):
    header = struct.pack('<I5HIH32sI', 0x0BEEF11E, 256, 56, 0, 4417, 54179,
                         0xffffffff, 2, b'Telink OTA Image'.ljust(32, b'\x00'), 70)
    stock = header + b'abcdef' + b'payload!'
    native = bytearray(stock)
    struct.pack_into('<H', native, 14, 65024)
    image = tmp_path / 'stock.ota'; image.write_bytes(stock)
    original = tmp_path / 'native.ota'; original.write_bytes(native)
    a = SimpleNamespace(image=str(image), native_image=str(original),
        sha256=hashlib.sha256(stock).hexdigest(), manufacturer_code=4417,
        image_type=54179, file_version=0xffffffff, url='http://example.invalid/test.ota')
    return a, stock


def test_verified_wrapper_and_http(tmp_path):
    a, binary = fixture(tmp_path)
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return binary
    with patch('bseed_targeted_z2m_ota.urllib.request.urlopen', return_value=FakeResponse()):
        actual, header = verify_image(a)
    assert actual == binary and header[5] == 54179


def test_response_with_matching_transaction_and_no_id_is_ours():
    from bseed_targeted_z2m_ota import matches_response
    token = 'tx-kitchen'
    assert matches_response({'status': 'error', 'data': {}, 'transaction': token}, token, 'KitchenSocketLeft', '0xa4c138241e3de538')
    assert not matches_response({'status': 'error', 'data': {}, 'transaction': 'other'}, token, 'KitchenSocketLeft', '0xa4c138241e3de538')
    assert matches_response({'status': 'ok', 'data': {'id': '0xa4c138241e3de538'}}, token, 'KitchenSocketLeft', '0xa4c138241e3de538')
    assert not matches_response({'status': 'ok', 'data': {'id': 'OtherSocket'}}, token, 'KitchenSocketLeft', '0xa4c138241e3de538')


def test_tampered_image_rejected_before_network(tmp_path):
    a, original = fixture(tmp_path)
    Path(a.image).write_bytes(original[:-1] + b'x')
    with pytest.raises(AssertionError, match='Image SHA'):
        verify_image(a)


def test_wrong_identity_and_payload_rejected_before_network(tmp_path):
    a, original = fixture(tmp_path)
    a.image_type = 65024
    with pytest.raises(AssertionError, match='Wrong OTA identity'):
        verify_image(a)
    a.image_type = 54179
    Path(a.native_image).write_bytes(original[:-1] + b'x')
    with pytest.raises(AssertionError, match='payload differs'):
        verify_image(a)
