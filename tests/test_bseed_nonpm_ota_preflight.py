"""Non-PM OTA preflight never bypasses the PM wattage or identity guard."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'helper_scripts'))
from bseed_targeted_z2m_ota import validate_metering_preflight
from bseed_ota_campaign import runner_args

IDENTITY = dict(model='TS011F-BS', manufacturer='o1jzcxou', role='EndDevice', max_reported_watts=1.0)


def check(state, *, non_pm=False, **overrides):
    return validate_metering_preflight(state, non_pm=non_pm, **(IDENTITY | overrides))


def test_nonpm_opt_in_only_for_exact_client_identity():
    assert check({'state_relay':'OFF'}, non_pm=True) is None
    for variant in ({'model':'TS011F-BS-PM'}, {'role':'Router'}, {'manufacturer':'b28wrpvx'}):
        with pytest.raises(AssertionError,match='exception only'):
            check({'state_relay':'OFF'}, non_pm=True, **variant)
    with pytest.raises(AssertionError,match='unexpectedly exposes PM'):
        check({'state_relay':'OFF','power':0},non_pm=True)


def test_pm_power_gate_still_requires_plausible_numeric_value():
    assert check({'power':0.5}) == 0.5
    for power in (None, True, -1, 2, float('nan'),float('inf')):
        with pytest.raises(AssertionError,match='Power missing'):
            check({'power':power})

TS0726 = dict(model='TS0726-3-BS', manufacturer='iedhxgyi', role='Router', max_reported_watts=1.0)


def ts_check(state, **overrides):
    return validate_metering_preflight(state, non_pm=False, ts0726=True, **(TS0726 | overrides))


def test_ts0726_opt_in_only_for_exact_router_identity():
    assert ts_check({'state': 'ON'}) is None
    for variant in ({'model': 'TS0726'}, {'role': 'EndDevice'}, {'manufacturer': 'b28wrpvx'}):
        with pytest.raises(AssertionError, match='exception only'):
            ts_check({'state': 'ON'}, **variant)
    with pytest.raises(AssertionError, match='unexpectedly exposes PM'):
        ts_check({'state': 'ON', 'power': 0})
    with pytest.raises(AssertionError, match='Conflicting'):
        validate_metering_preflight({'state': 'ON'}, non_pm=True, ts0726=True, **TS0726)


def test_ts0726_stock_conversion_identity_uses_same_metering_exception():
    assert ts_check({'state': 'ON'}, model='TS0726', manufacturer='_TZ3002_iedhxgyi') is None


def test_campaign_adds_ts0726_exemption_for_ts0726_board_only():
    profile = {k: 'TEST' for k in ('device', 'ieee', 'manufacturer', 'model', 'preflash_role', 'image', 'sha256', 'url', 'mqtt_config', 'broker', 'workdir', 'manufacturer_code', 'image_type', 'file_version', 'expect_relay', 'index_url')}
    assert '--ts0726' not in runner_args(profile, 'preflight')
    profile['manufacturer'] = 'iedhxgyi'
    assert runner_args(profile, 'preflight').count('--ts0726') == 1
    profile['manufacturer'] = '_TZ3002_iedhxgyi'
    assert runner_args(profile, 'preflight').count('--ts0726') == 1
    profile['manufacturer'] = 'b28wrpvx'
    assert '--ts0726' not in runner_args(profile, 'preflight')
    profile['manufacturer'] = 'iedhxgyi'
    profile['non_pm'] = True
    profile['preflash_build'] = '1.1.8-bseedv8'
    profile['preflash_relay_physical_mode'] = 'follow_state'
    with pytest.raises(ValueError, match='must not set the socket non-PM mode'):
        runner_args(profile, 'preflight')


def test_campaign_only_adds_exemption_when_explicitly_enabled():
    profile={k:'TEST' for k in ('device','ieee','manufacturer','model','preflash_role','image','sha256','url','mqtt_config','broker','workdir','manufacturer_code','image_type','file_version','expect_relay','index_url')}
    profile['require_pm']=True
    assert '--non-pm' not in runner_args(profile,'preflight')
    profile['require_pm']=False
    assert '--non-pm' not in runner_args(profile,'preflight')
    profile['non_pm']=True
    profile['preflash_build']='1.1.2-bseedcli4'
    profile['preflash_relay_physical_mode']='follow_state'
    args=runner_args(profile,'preflight')
    assert args.count('--non-pm')==1
