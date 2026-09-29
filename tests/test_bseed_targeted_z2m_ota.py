"""Offline regression tests; never connects to MQTT or flashes a device."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_targeted_z2m_ota import verify_image, write_live_status
from tests.bseed_image_fixture import image_for


def test_live_status_new_transaction_drops_previous_failure(tmp_path):
    write_live_status(tmp_path, token='old', phase='update_error',
                      response={'transaction': 'old', 'status': 'error'},
                      error='old timeout', update={'progress': 50}, ota_was_sent=True)
    current = write_live_status(tmp_path, token='new', phase='ota_running',
                                image_sha256='new-image', block_bytes=32)
    assert current['token'] == 'new'
    assert current['phase'] == 'ota_running'
    assert not {'response', 'error', 'update', 'ota_was_sent'} & current.keys()
    assert json.loads((tmp_path / 'LIVE_STATUS.json').read_text()) == current


@pytest.mark.parametrize('phase', [
    'ota_transfer_ok_postflash_unverified', 'update_error',
    'update_timeout_or_unconfirmed', 'preflight_abort',
])
def test_live_status_late_observation_preserves_terminal_result(tmp_path, phase):
    response = {'transaction': 'same', 'status': 'ok' if phase.startswith('ota_transfer') else 'error'}
    write_live_status(tmp_path, token='same', phase=phase, response=response,
                      image_sha256='image', block_bytes=32)
    current = write_live_status(tmp_path, token='same', phase='ota_running',
                                update={'progress': 100})
    assert current['phase'] == phase
    assert current['response'] == response
    assert current['image_sha256'] == 'image' and current['block_bytes'] == 32
    assert current['update']['progress'] == 100


def test_live_status_concurrent_callback_writes_preserve_each_update(tmp_path):
    def write_sample(sequence):
        return write_live_status(tmp_path, token='same', phase='ota_running',
                                 **{f'sample_{sequence}': sequence})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_sample, range(16)))
    current = json.loads((tmp_path / 'LIVE_STATUS.json').read_text())
    assert all(current[f'sample_{sequence}'] == sequence for sequence in range(16))
    assert not list(tmp_path.glob('LIVE_STATUS.json.*.tmp'))


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


def test_nonpm_force_wrapper_validates_embedded_native_version_not_ffffffff(tmp_path):
    candidate = dict(build='1.1.3-bseedc7', version=0x11023014, type=65026)
    native = image_for(candidate)
    wrapper = bytearray(native)
    struct.pack_into('<H', wrapper, 12, 43555)
    struct.pack_into('<I', wrapper, 14, 0xffffffff)
    wrapper = bytes(wrapper)
    image = tmp_path / 'forced.ota'; image.write_bytes(wrapper)
    native_path = tmp_path / 'native.ota'; native_path.write_bytes(native)
    args = SimpleNamespace(
        image=str(image), native_image=str(native_path), non_pm=True,
        sha256=hashlib.sha256(wrapper).hexdigest(), manufacturer_code=4417,
        image_type=43555, file_version=0xffffffff,
        url='http://example.invalid/forced.ota')

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return wrapper

    with patch('bseed_targeted_z2m_ota.urllib.request.urlopen', return_value=FakeResponse()):
        actual, header = verify_image(args)
    assert actual == wrapper and header[6] == 0xffffffff


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


def test_update_payload_paced_profile_for_sleepy_end_device():
    from bseed_targeted_z2m_ota import update_payload
    data = update_payload('0xa4c13824a7005afb', 'http://example.invalid/client.ota', 'transaction-2', 48, 1200)
    assert data['image_block_response_delay'] == 1200
    assert data['default_maximum_data_size'] == 48
    legacy = update_payload('0xa4c13824a7005afb', 'http://example.invalid/client.ota', 'transaction-3', 48)
    assert 'image_block_response_delay' not in legacy
    assert legacy['image_block_request_timeout'] == 600000
    paced = update_payload('0xa4c13824a7005afb', 'http://example.invalid/client.ota', 'transaction-4', 48, 1200, 1800000)
    assert paced['image_block_request_timeout'] == 1800000
    with pytest.raises(AssertionError, match='60000..3600000'):
        update_payload('target', 'url', 'token', 48, None, 59999)
    with pytest.raises(AssertionError, match='60000..3600000'):
        update_payload('target', 'url', 'token', 48, None, 3600001)
    with pytest.raises(AssertionError, match='0..10000'):
        update_payload('target', 'url', 'token', 48, 10001)
    with pytest.raises(AssertionError, match='0..10000'):
        update_payload('target', 'url', 'token', 48, -1)


def test_update_payload_uses_explicit_bounded_block_size():
    from bseed_targeted_z2m_ota import update_payload
    data = update_payload('0xa4c138241e3de538', 'http://example.invalid/client.ota', 'transaction-1', 50)
    assert data == {'id': '0xa4c138241e3de538', 'url': 'http://example.invalid/client.ota', 'transaction': 'transaction-1', 'image_block_request_timeout': 600000, 'default_maximum_data_size': 50}
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
    assert new_campaign_allowed({'phase':'installed_image_reconciled'})
    assert new_campaign_allowed({'phase':'source_unchanged_reconciled'})
    assert new_campaign_allowed({'phase':'preflight_abort'})
    assert ota_transport_phase({'status':'error'})=='update_error'
    assert ota_transport_phase({})=='update_timeout_or_unconfirmed'


def test_flash_uses_single_shared_network_authority_before_submit():
    src = (Path(__file__).resolve().parents[1] / 'helper_scripts/bseed_targeted_z2m_ota.py').read_text()
    assert 'shared_network_lock_path(source_profile, required=True)' in src
    assert 'require_profile_authority' not in src
    assert src.index('shared_network_lock_path(source_profile, required=True)') < src.index('acquire_network_lock(')
    assert src.index('acquire_network_lock(') < src.index('payload = update_payload(')
    assert src.index('if active_updates:') < src.index('acquire_network_lock(')
