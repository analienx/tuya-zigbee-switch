"""OTA block size: 48 bytes only for custom-firmware downloads with paced responses."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_ota_block_policy import (  # noqa: E402
    GATE_BLOCK_BYTES, check_block_bytes, stock_source,
)
import bseed_ota_campaign as campaign  # noqa: E402


def custom(**changes):
    return dict({'manufacturer': 'b28wrpvx', 'block_bytes': 48, 'response_delay_ms': 1200},
                **changes)


def test_custom_firmware_takes_48_bytes_when_paced():
    assert check_block_bytes(custom()) == 48
    assert check_block_bytes(custom(response_delay_ms=1800)) == 48
    assert check_block_bytes(custom(), allowed=GATE_BLOCK_BYTES) == 48


@pytest.mark.parametrize('delay', [None, 0, 250, 1199, True, '1200'])
def test_48_bytes_unpaced_is_refused(delay):
    with pytest.raises(ValueError, match='response_delay_ms'):
        check_block_bytes(custom(response_delay_ms=delay))


@pytest.mark.parametrize('manufacturer', ['_TZ3000_b28wrpvx', '_TZ3002_iedhxgyi'])
def test_stock_first_conversion_stays_at_32(manufacturer):
    assert stock_source({'manufacturer': manufacturer})
    with pytest.raises(ValueError, match='Stock firmware'):
        check_block_bytes(custom(manufacturer=manufacturer))
    assert check_block_bytes(custom(manufacturer=manufacturer, block_bytes=32)) == 32


def test_32_bytes_needs_no_pacing_and_absent_size_is_left_to_the_runner():
    assert check_block_bytes({'manufacturer': '_TZ3000_b28wrpvx', 'block_bytes': 32}) == 32
    assert check_block_bytes({'manufacturer': 'b28wrpvx'}) is None
    with pytest.raises(ValueError, match='explicitly'):
        check_block_bytes({'manufacturer': 'b28wrpvx'}, allowed=GATE_BLOCK_BYTES)


@pytest.mark.parametrize('block', [16, 40, 50, 64, True, 'x'])
def test_gates_admit_only_32_or_48(block):
    with pytest.raises(ValueError):
        check_block_bytes(custom(block_bytes=block), allowed=GATE_BLOCK_BYTES)


def _profile_file(tmp_path, **changes):
    image = tmp_path / 'image.ota'
    image.write_bytes(b'fixture')
    profile = {
        'device': 'TestSocket', 'ieee': '0x0011223344556677',
        'manufacturer': 'b28wrpvx', 'model': 'TS011F-BS-PM', 'require_pm': True,
        'preflash_role': 'Router', 'postflash_role': 'Router', 'image': str(image),
        'sha256': '0' * 64, 'url': 'http://example.invalid/i.ota',
        'mqtt_config': str(tmp_path / 'mqtt.yaml'), 'broker': '127.0.0.1',
        'workdir': str(tmp_path / 'work'), 'manufacturer_code': 4417,
        'image_type': 43556, 'file_version': '0x12053025', 'expect_relay': 'ON',
        'index_url': 'http://example.invalid/x.json',
        'index_output': str(tmp_path / 'index.json'),
        'template_index': str(tmp_path / 'template.json'),
        'postflash_build': '1.2.5-bseedr23', 'block_bytes': 48, 'response_delay_ms': 1200,
    }
    profile.update(changes)
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    return path


def test_load_profile_applies_the_policy_to_every_campaign(tmp_path):
    assert campaign.load_profile(_profile_file(tmp_path))['block_bytes'] == 48
    with pytest.raises(ValueError, match='response_delay_ms'):
        campaign.load_profile(_profile_file(tmp_path, response_delay_ms=None))
    with pytest.raises(ValueError, match='Stock firmware'):
        campaign.load_profile(_profile_file(tmp_path, manufacturer='_TZ3000_b28wrpvx',
                                            model='TS011F', image_type=54179,
                                            file_version='0xffffffff'))
