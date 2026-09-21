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

def test_campaign_only_adds_exemption_when_explicitly_enabled():
    profile={k:'TEST' for k in ('device','ieee','manufacturer','model','preflash_role','image','sha256','url','mqtt_config','broker','workdir','manufacturer_code','image_type','file_version','expect_relay','index_url')}
    profile['require_pm']=True
    assert '--non-pm' not in runner_args(profile,'preflight')
    profile['require_pm']=False
    assert '--non-pm' not in runner_args(profile,'preflight')
    profile['non_pm']=True
    args=runner_args(profile,'preflight')
    assert args.count('--non-pm')==1
