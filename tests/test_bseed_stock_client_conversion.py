"""Stock PM socket -> custom PM mains Client in one OTA: profile, runner and telemetry gates."""
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_ota_campaign as campaign  # noqa: E402
import bseed_pm_telemetry_guard as guard  # noqa: E402
from bseed_targeted_z2m_ota import require_physical_preflight_board  # noqa: E402


def profile(tmp_path, **changes):
    image = tmp_path / 'wrapper.ota'
    image.write_bytes(b'wrapper fixture')
    native = tmp_path / 'native.ota'
    native.write_bytes(b'native fixture')
    data = {
        'device': 'TestStockSocket', 'ieee': '0x0011223344556677',
        'manufacturer': '_TZ3000_b28wrpvx', 'model': 'TS011F',
        'preflash_role': 'Router', 'postflash_role': 'EndDevice', 'require_pm': True,
        'stock_client_conversion': True, 'pm_preflash_load_proof': 'physically_unloaded',
        'image': str(image), 'sha256': '1' * 64,
        'native_image': str(native), 'native_sha256': '2' * 64,
        'url': 'http://example.invalid/w.ota', 'mqtt_config': str(tmp_path / 'mqtt.yaml'),
        'broker': '127.0.0.1', 'workdir': str(tmp_path / 'work'),
        'manufacturer_code': 4417, 'image_type': 54179, 'file_version': '0xffffffff',
        'expect_relay': 'OFF', 'relay_get_key': 'state',
        'index_url': 'http://example.invalid/i.json', 'index_output': str(tmp_path / 'index.json'),
        'template_index': str(tmp_path / 'template.json'),
        'postflash_build': '1.2.5-bseedcli26', 'join_via': 'KnownRouter',
        'block_bytes': 48, 'response_delay_ms': 1200,
    }
    data.update(changes)
    return data


def write(tmp_path, data):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(data))
    return path


def test_stock_client_profile_loads_with_physical_proof_and_48_byte_blocks(tmp_path):
    loaded = campaign.load_profile(write(tmp_path, profile(tmp_path)))
    assert loaded['stock_client_conversion'] is True and loaded['block_bytes'] == 48


@pytest.mark.parametrize('changes, match', [
    ({'stock_client_conversion': 'yes'}, 'explicit Boolean'),
    ({'manufacturer': '_TZ3000_o1jzcxou'}, 'manufacturer'),
    ({'postflash_role': 'Router'}, 'postflash_role'),
    ({'pm_preflash_load_proof': 'meter'}, 'pm_preflash_load_proof'),
    ({'require_pm': False}, 'require_pm'),
    ({'native_sha256': None}, 'native_image and native_sha256'),
    ({'preflash_build': '1.0.13'}, 'preflash_build'),
    ({'join_via': None}, 'join_via'),
    ({'force_test_transition': True}, 'non-FORCE'),
])
def test_stock_client_profile_rejects_any_other_shape(tmp_path, changes, match):
    with pytest.raises(ValueError, match=match):
        campaign.load_profile(write(tmp_path, profile(tmp_path, **changes)))


def test_physical_proof_stays_refused_for_other_stock_profiles(tmp_path):
    data = profile(tmp_path, stock_client_conversion=False)
    with pytest.raises(ValueError, match='Physical PM preflash load proof'):
        campaign.load_profile(write(tmp_path, data))


def runner_args(**changes):
    return SimpleNamespace(**dict(pm_preflash_physical_unloaded=True, non_pm=False, ts0726=False,
                                  manufacturer='_TZ3000_b28wrpvx', model='TS011F'), **changes)


def test_runner_physical_preflight_board_rule():
    stock_campaign = {'stock_client_conversion': True}
    require_physical_preflight_board(runner_args(), stock_campaign)
    require_physical_preflight_board(runner_args(manufacturer='b28wrpvx', model='TS011F-BS-PM'), None)
    require_physical_preflight_board(runner_args(pm_preflash_physical_unloaded=False), None)
    for args, camp in ((runner_args(), None), (runner_args(), {}),
                       (runner_args(model='TS011F-BS-PM'), stock_campaign),
                       (runner_args(non_pm=True), stock_campaign),
                       (runner_args(manufacturer='_TZ3000_o1jzcxou'), stock_campaign)):
        with pytest.raises(ValueError):
            require_physical_preflight_board(args, camp)


def test_telemetry_guard_records_no_quarantine_and_releases_on_verified_client(tmp_path, monkeypatch):
    data = profile(tmp_path)
    Path(data['workdir']).mkdir()
    monkeypatch.setattr(guard, 'bridge_for', lambda *a: (_ for _ in ()).throw(
        AssertionError('stock conversion must not touch converter options')))
    guard.begin(data, 'token-1')
    record = json.loads(guard.guard_path(data).read_text())
    assert record['quarantine_enabled'] is False and record['phase'] == guard.STOCK_NO_QUARANTINE_PHASE
    with pytest.raises(ValueError, match='existing telemetry guard'):
        guard.begin(data, 'token-2')

    work = Path(data['workdir'])
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        ieee=data['ieee'], sha256=data['sha256'], token='token-1',
        phase='ota_transfer_ok_postflash_unverified')))
    evidence = tmp_path / 'metadata.json'
    body = dict(at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)).isoformat(),
                ieee=data['ieee'], result='metadata_refreshed', error=None, interview_ok=True,
                fresh_inventory_observed=True, live_zdo_after={'role': 'EndDevice'},
                after=dict(ieee_address=data['ieee'], software_build_id=data['postflash_build'],
                           type='EndDevice'))
    evidence.write_text(json.dumps(dict(body, live_zdo_after={'role': 'Router'})))
    with pytest.raises(ValueError, match='live role'):
        guard.release(data, evidence)
    evidence.write_text(json.dumps(body))
    guard.release(data, evidence)
    assert not guard.guard_path(data).exists()
    released = json.loads((work / 'PM_TELEMETRY_RELEASED_token-1.json').read_text())
    assert released['phase'] == 'released_no_quarantine_option'
