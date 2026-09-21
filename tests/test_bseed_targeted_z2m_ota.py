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


def test_update_payload_uses_explicit_bounded_block_size():
    from bseed_targeted_z2m_ota import update_payload
    data = update_payload('0xa4c138241e3de538', 'http://example.invalid/client.ota', 'transaction-1', 50)
    assert data == {'id': '0xa4c138241e3de538', 'url': 'http://example.invalid/client.ota', 'transaction': 'transaction-1', 'image_block_request_timeout': 1800000, 'default_maximum_data_size': 50}
    with pytest.raises(AssertionError, match='10..100'):
        update_payload('target', 'url', 'token', 101)
    with pytest.raises(AssertionError, match='10..100'):
        update_payload('target', 'url', 'token', 9)


def test_readonly_check_wait_outlasts_z2m_device_timeout():
    from bseed_targeted_z2m_ota import DEFAULT_CHECK_TIMEOUT_SECONDS, wait_for_check_result
    from unittest.mock import Mock
    done = Mock()
    done.wait.return_value = True
    assert DEFAULT_CHECK_TIMEOUT_SECONDS >= 70
    assert wait_for_check_result(done, DEFAULT_CHECK_TIMEOUT_SECONDS)
    done.wait.assert_called_once_with(DEFAULT_CHECK_TIMEOUT_SECONDS)
    with pytest.raises(AssertionError, match='outlast Zigbee2MQTT'):
        wait_for_check_result(done, 55)


def test_transport_ok_is_not_postflash_accepted():
    from bseed_targeted_z2m_ota import new_campaign_allowed, ota_transport_phase
    phase=ota_transport_phase({'status':'ok','data':{'to':{'file_version':4294967295}}})
    assert phase=='ota_transfer_ok_postflash_unverified'
    assert not new_campaign_allowed({'phase':phase})
    assert not new_campaign_allowed({'phase':'update_ok'})  # legacy transfer-only state
    assert not new_campaign_allowed({'phase':'update_error'})
    assert not new_campaign_allowed({'phase':'update_timeout_or_unconfirmed'})
    assert new_campaign_allowed({'phase':'postflash_accepted'})
    assert new_campaign_allowed({'phase':'preflight_abort'})
    assert ota_transport_phase({'status':'error'})=='update_error'
    assert ota_transport_phase({})=='update_timeout_or_unconfirmed'


def test_ota_inactivity_timeout_configurable_and_bounded():
    from bseed_targeted_z2m_ota import update_payload
    args=('target','http://example.invalid/image.ota','transaction',32)
    assert update_payload(*args,2100000)['image_block_request_timeout'] == 2100000
    for value in (149999,3600001):
        with pytest.raises(AssertionError,match='inactivity timeout'):
            update_payload(*args,value)

def test_campaign_transmits_timeout_without_creating_automatic_retry():
    from bseed_ota_campaign import runner_args
    p={'device':'BedroomSocketCabinetRight', 'ieee':'0xa4c13824a7005afb', 'non_pm':False,
       'image':'i', 'url':'u','index_url':'idx','mqtt_config':'m','broker':'b','workdir':'w',
       'manufacturer':'o1jzcxou','model':'TS011F-BS','preflash_role':'EndDevice',
       'sha256':'a'*64,'manufacturer_code':4417,'image_type':65026,
       'file_version':'0x11023013','expect_relay':'OFF','block_request_timeout_ms':2100000}
    cmd=runner_args(p,'check')
    assert cmd[cmd.index('--block-request-timeout-ms')+1]=='2100000'
