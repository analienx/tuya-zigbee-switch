"""Offline tests for the stock->custom PM Router OTA conversion gate.

A brand-new stock Router has no way to request its real custom image type,
so its first custom flash must travel inside a stock-accepted wrapper. These
tests cover the wrapper byte-proof and the profile/gate rules around it;
no live MQTT, HTTP or firmware changes.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_campaign as campaign

STOCK_MANUFACTURER = '_TZ3000_b28wrpvx'
STOCK_MODEL = 'TS011F'
MANUFACTURER_CODE = 4417
STOCK_IMAGE_TYPE = 54179
NATIVE_IMAGE_TYPE = 43556
CLIENT_IMAGE_TYPE = 65024


def make_ota(image_type, file_version, payload=b'payload bytes here', size=None):
    header = struct.pack('<IHHH', 0x0BEEF11E, 256, 56, 0)
    header += struct.pack('<HHI', MANUFACTURER_CODE, image_type, file_version)
    header += struct.pack('<H', 2) + b'Telink OTA Image'.ljust(32, b'\x00')
    blob = bytearray(header) + payload
    size = size or len(blob) + 4
    blob += struct.pack('<I', size)
    blob = blob.ljust(size, b'\x00')
    return bytes(blob[:52]) + struct.pack('<I', size) + bytes(blob[56:])


def wrapper_from_native(native, image_type=STOCK_IMAGE_TYPE, file_version=0xFFFFFFFF):
    wrapped = bytearray(native)
    struct.pack_into('<H', wrapped, 12, image_type)
    struct.pack_into('<I', wrapped, 14, file_version)
    return bytes(wrapped)


def write(path, data):
    path.write_bytes(data)
    return path


def router_matrix(tmp_path, native_sha256, build='1.2.5-bseedr23'):
    manifest = tmp_path / 'ROLE_MATRIX.json'
    manifest.write_text(json.dumps({
        'hardwareAcceptance': False, 'compiledBothRoles': True,
        'artifacts': [
            {'role': 'Router', 'build': build, 'imageType': NATIVE_IMAGE_TYPE,
             'sha256': native_sha256},
            {'role': 'EndDevice', 'build': '1.2.5-bseedcli26', 'imageType': CLIENT_IMAGE_TYPE,
             'sha256': 'f' * 64},
        ]}))
    return manifest


def stock_profile(tmp_path, *, build='1.2.5-bseedr23'):
    native = make_ota(NATIVE_IMAGE_TYPE, 302329893)
    wrapper = wrapper_from_native(native)
    native_path = write(tmp_path / 'native.ota', native)
    wrapper_path = write(tmp_path / 'wrapper.ota', wrapper)
    native_sha256 = hashlib.sha256(native).hexdigest()
    key = tmp_path / 'ssh.key'
    key.write_text('fixture')
    return {
        'device': 'TestStockRouter', 'ieee': '0x0011223344556677',
        'manufacturer': STOCK_MANUFACTURER, 'model': STOCK_MODEL,
        'preflash_role': 'Router', 'postflash_role': 'Router',
        'require_pm': True, 'relay_get_key': 'state_relay',
        'stock_router_conversion': True,
        'image': str(wrapper_path), 'sha256': hashlib.sha256(wrapper).hexdigest(),
        'manufacturer_code': MANUFACTURER_CODE, 'image_type': STOCK_IMAGE_TYPE,
        'file_version': '0xffffffff', 'postflash_build': build,
        'native_image': str(native_path), 'native_sha256': native_sha256,
        'pm_router_matrix_evidence': str(router_matrix(tmp_path, native_sha256, build=build)),
        'pm_ssh_host': '127.0.0.1', 'pm_ssh_key': str(key),
    }


def test_wrapper_proof_accepts_header_only_diff(tmp_path):
    profile = stock_profile(tmp_path)
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    router_entry = matrix['artifacts'][0]
    assert campaign.verify_stock_router_wrapper(profile, router_entry) is None


@pytest.mark.parametrize('mutate, match', [
    (lambda b: bytearray(b[:-1]) + b'\x01', 'preserve the entire'),  # payload byte changed
    # Flips the header_length field (offset 6) -- inside the 56-byte header
    # but outside the image-type/file-version bytes (12..17) a stock wrapper
    # is allowed to change.
    (lambda b: (lambda w: (struct.pack_into('<H', w, 6, 0xDEAD), bytes(w))[1])(bytearray(b)),
     'only change'),
])
def test_wrapper_proof_rejects_payload_or_extra_header_change(tmp_path, mutate, match):
    profile = stock_profile(tmp_path)
    wrapper = Path(profile['image']).read_bytes()
    tampered = bytes(mutate(wrapper))
    Path(profile['image']).write_bytes(tampered)
    profile['sha256'] = hashlib.sha256(tampered).hexdigest()
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    with pytest.raises(ValueError, match=match):
        campaign.verify_stock_router_wrapper(profile, matrix['artifacts'][0])


def test_wrapper_proof_rejects_non_sentinel_file_version(tmp_path):
    profile = stock_profile(tmp_path)
    native = Path(profile['native_image']).read_bytes()
    wrapper = wrapper_from_native(native, file_version=0x00000001)
    Path(profile['image']).write_bytes(wrapper)
    profile['sha256'] = hashlib.sha256(wrapper).hexdigest()
    profile['file_version'] = '0x00000001'
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    with pytest.raises(ValueError, match='sentinel'):
        campaign.verify_stock_router_wrapper(profile, matrix['artifacts'][0])


def test_wrapper_proof_rejects_native_sha256_mismatch(tmp_path):
    profile = stock_profile(tmp_path)
    profile['native_sha256'] = 'f' * 64
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    with pytest.raises(ValueError, match='native_sha256'):
        campaign.verify_stock_router_wrapper(profile, matrix['artifacts'][0])


def test_wrapper_proof_rejects_native_not_matching_matrix(tmp_path):
    profile = stock_profile(tmp_path, build='1.2.5-bseedr23')
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    matrix['artifacts'][0]['build'] = '1.2.5-bseedr99'
    with pytest.raises(ValueError, match='validated matrix'):
        campaign.verify_stock_router_wrapper(profile, matrix['artifacts'][0])


def test_wrapper_proof_requires_native_image_file(tmp_path):
    profile = stock_profile(tmp_path)
    Path(profile['native_image']).unlink()
    matrix = json.loads(Path(profile['pm_router_matrix_evidence']).read_text())
    with pytest.raises(ValueError, match='native image'):
        campaign.verify_stock_router_wrapper(profile, matrix['artifacts'][0])


def test_verified_router_pm_candidate_accepts_stock_conversion_without_baseline(tmp_path):
    profile = stock_profile(tmp_path)
    assert 'pm_settings_baseline' not in profile
    assert campaign.verified_router_pm_candidate(profile) is True


def test_verified_router_pm_candidate_stock_path_rejects_settings_baseline(tmp_path):
    profile = stock_profile(tmp_path)
    baseline = tmp_path / 'baseline.json'
    baseline.write_text('{}')
    profile['pm_settings_baseline'] = str(baseline)
    with pytest.raises(ValueError, match='no genuine prior'):
        campaign.verified_router_pm_candidate(profile)


def test_verified_router_pm_candidate_native_path_still_requires_baseline_file(tmp_path):
    profile = stock_profile(tmp_path)
    profile['stock_router_conversion'] = False
    profile['image_type'] = NATIVE_IMAGE_TYPE
    profile['image'] = profile['native_image']
    profile['sha256'] = profile['native_sha256']
    with pytest.raises(ValueError, match='baseline'):
        campaign.verified_router_pm_candidate(profile)
    profile['pm_settings_baseline'] = str(tmp_path / 'missing.json')
    with pytest.raises(ValueError, match='baseline'):
        campaign.verified_router_pm_candidate(profile)


@pytest.mark.parametrize('mutation, match', [
    ({'preflash_role': 'EndDevice'}, 'unchanged'),
    ({'manufacturer': 'b28wrpvx'}, 'stock manufacturer'),
    ({'require_pm': False}, 'PM-board-only'),
    ({'force_test_transition': True}, 'mutually exclusive'),
])
def test_load_profile_rejects_unsafe_stock_router_settings(tmp_path, mutation, match):
    profile = stock_profile(tmp_path)
    profile.update(mutation)
    profile.update({
        'image_type': STOCK_IMAGE_TYPE, 'url': 'http://example.invalid/w.ota',
        'broker': '127.0.0.1',
        'mqtt_config': str(write(tmp_path / 'mqtt.yaml', b'mqtt: {}')),
        'workdir': str(tmp_path / 'campaign'),
        'expect_relay': 'ON', 'index_url': 'http://example.invalid/i.json',
        'index_output': str(tmp_path / 'index.json'),
        'template_index': str(write(tmp_path / 'template.json', b'[]')),
    })
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match=match):
        campaign.load_profile(path)


def test_load_profile_requires_native_fields_for_stock_conversion(tmp_path):
    profile = stock_profile(tmp_path)
    del profile['native_sha256']
    profile.update({
        'url': 'http://example.invalid/w.ota', 'broker': '127.0.0.1',
        'mqtt_config': str(write(tmp_path / 'mqtt.yaml', b'mqtt: {}')),
        'workdir': str(tmp_path / 'campaign'),
        'expect_relay': 'ON', 'index_url': 'http://example.invalid/i.json',
        'index_output': str(tmp_path / 'index.json'),
        'template_index': str(write(tmp_path / 'template.json', b'[]')),
    })
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match='native_image and native_sha256'):
        campaign.load_profile(path)


def test_stock_router_flash_dispatch_order_matches_native_router_flash(tmp_path, monkeypatch):
    profile = stock_profile(tmp_path)
    profile.update({
        'url': 'http://example.invalid/w.ota', 'broker': '127.0.0.1',
        'mqtt_config': str(write(tmp_path / 'mqtt.yaml', b'mqtt: {}')),
        'workdir': str(tmp_path / 'campaign'),
        'expect_relay': 'ON', 'index_url': 'http://example.invalid/i.json',
        'index_output': str(tmp_path / 'index.json'),
        'template_index': str(write(tmp_path / 'template.json', b'[]')),
    })
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'flash',
                                      '--confirm-ieee', profile['ieee']])
    monkeypatch.setattr('bseed_pm_telemetry_guard.release', lambda *a: None)
    with patch('bseed_ota_campaign.subprocess.call', return_value=0) as run, \
         patch('bseed_ota_campaign.record_postflash_candidate', return_value=True):
        with pytest.raises(SystemExit) as done:
            campaign.main()
    assert done.value.code == 0
    # preserve_device_state is off in this fixture (covered generically and
    # independently of stock_router_conversion in
    # test_bseed_campaign_state_preservation.py); the dispatch shape below is
    # otherwise identical to an already-custom Router's same-role refresh.
    names = [Path(c.args[0][2]).name for c in run.call_args_list]
    assert names == ['bseed_targeted_z2m_ota.py', 'bseed_z2m_postota_reinterview.py',
                     'bseed_z2m_postflash_verify.py', 'bseed_pm_role_audit.py']
