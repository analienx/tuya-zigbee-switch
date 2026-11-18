"""OTA block size: 48 bytes whenever responses are paced, custom or stock firmware."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_ota_block_policy import (  # noqa: E402
    GATE_BLOCK_BYTES, check_block_bytes, default_block_bytes,
)
import bseed_ota_campaign as campaign  # noqa: E402


def paced(**changes):
    return dict({'manufacturer': 'b28wrpvx', 'block_bytes': 48, 'response_delay_ms': 1200},
                **changes)


@pytest.mark.parametrize('manufacturer', ['b28wrpvx', 'o1jzcxou', '_TZ3000_b28wrpvx'])
def test_48_bytes_when_paced_for_custom_and_stock_firmware(manufacturer):
    assert check_block_bytes(paced(manufacturer=manufacturer)) == 48
    assert check_block_bytes(paced(manufacturer=manufacturer, response_delay_ms=1800)) == 48
    assert check_block_bytes(paced(manufacturer=manufacturer), allowed=GATE_BLOCK_BYTES) == 48


@pytest.mark.parametrize('delay', [None, 0, 250, 1199, True, '1200'])
@pytest.mark.parametrize('manufacturer', ['b28wrpvx', '_TZ3000_b28wrpvx'])
def test_48_bytes_unpaced_is_refused(delay, manufacturer):
    with pytest.raises(ValueError, match='response_delay_ms'):
        check_block_bytes(paced(manufacturer=manufacturer, response_delay_ms=delay))


def test_32_bytes_needs_no_pacing_and_absent_size_is_accepted_unchecked():
    assert check_block_bytes({'manufacturer': '_TZ3000_b28wrpvx', 'block_bytes': 32}) == 32
    assert check_block_bytes({'manufacturer': 'b28wrpvx'}) is None
    with pytest.raises(ValueError, match='explicitly'):
        check_block_bytes({'manufacturer': 'b28wrpvx'}, allowed=GATE_BLOCK_BYTES)


@pytest.mark.parametrize('block', [16, 40, 50, 64, True, 'x'])
def test_gates_admit_only_32_or_48(block):
    with pytest.raises(ValueError):
        check_block_bytes(paced(block_bytes=block), allowed=GATE_BLOCK_BYTES)


@pytest.mark.parametrize('profile,expected', [
    ({'manufacturer': 'b28wrpvx', 'response_delay_ms': 1200}, 48),
    ({'manufacturer': '_TZ3000_b28wrpvx', 'response_delay_ms': 1800}, 48),
    ({'manufacturer': 'b28wrpvx', 'response_delay_ms': 250}, 32),
    ({'manufacturer': '_TZ3000_b28wrpvx'}, 32),
])
def test_unset_block_size_defaults_to_the_largest_proven_size(profile, expected):
    assert default_block_bytes(profile) == expected


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


STOCK = dict(manufacturer='_TZ3000_b28wrpvx', model='TS011F', image_type=54179,
             file_version='0xffffffff')


def test_load_profile_fills_an_unset_block_size_so_the_runner_never_guesses(tmp_path):
    loaded = campaign.load_profile(_profile_file(tmp_path, block_bytes=None))
    assert loaded['block_bytes'] == 48
    cmd = campaign.runner_args(loaded, 'check')
    assert cmd[cmd.index('--max-block-bytes') + 1] == '48'
    assert campaign.load_profile(_profile_file(tmp_path, block_bytes=None, **STOCK))[
        'block_bytes'] == 48
    assert campaign.load_profile(_profile_file(
        tmp_path, block_bytes=None, response_delay_ms=None, **STOCK))['block_bytes'] == 32


def test_load_profile_applies_the_policy_to_every_campaign(tmp_path):
    assert campaign.load_profile(_profile_file(tmp_path))['block_bytes'] == 48
    assert campaign.load_profile(_profile_file(tmp_path, **STOCK))['block_bytes'] == 48
    with pytest.raises(ValueError, match='response_delay_ms'):
        campaign.load_profile(_profile_file(tmp_path, response_delay_ms=None))
    with pytest.raises(ValueError, match='response_delay_ms'):
        campaign.load_profile(_profile_file(tmp_path, response_delay_ms=None, **STOCK))
