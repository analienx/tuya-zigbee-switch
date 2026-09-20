"""Offline postflash acceptance tests; never connect to a live Zigbee network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_z2m_postflash_verify import evaluate

IEEE='0xa4c138241e3de538'
def record(role='EndDevice', build='1.2.5-bseedcli6', interviewed=True):
    return {'ieee_address': IEEE, 'friendly_name':'KitchenSocketLeft',
            'type':role, 'software_build_id':build, 'interview_completed':interviewed}

def test_old_cached_router_with_successful_ota_must_not_pass():
    verdict, issues, _=evaluate([record('Router',None,False)],None,IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='unconfirmed' and 'interview not completed' in issues
    assert 'live ZDO role not verified' in issues

def test_old_build_must_not_pass_even_if_role_changes():
    verdict,issues,_=evaluate([record(build='old')],{'device':{'ieeeAddr':IEEE}},IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='unconfirmed' and 'expected firmware build not verified' in issues

def test_candidate_still_requires_independent_physical_acceptance():
    verdict,issues,_=evaluate([record()],{'device':{'ieeeAddr':IEEE},'state_relay':'ON'},IEEE,'EndDevice','1.2.5-bseedcli6', {'role':'EndDevice'})
    assert verdict=='postflash_candidate' and issues==[]

def test_no_passive_state_but_live_zdo_read_is_candidate_only():
    verdict,issues,_=evaluate([record('Router')],None,IEEE,'EndDevice','1.2.5-bseedcli6',{'role':'EndDevice'})
    assert verdict=='postflash_candidate' and issues==[]

def test_live_descriptor_is_required_despite_cached_enddevice_role():
    verdict,issues,_=evaluate([record()],None,IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='unconfirmed' and 'live ZDO role not verified' in issues


def pm_device(reporting=True):
    device=record()
    device['endpoints']={'1':{'configured_reportings': ([{'cluster':'haElectricalMeasurement',
        'attribute':'activePower','minimum_report_interval':10,'maximum_report_interval':60,
        'reportable_change':5}] if reporting else [])}}
    return device

def test_pm_gate_catches_missing_report_and_raw_unscaled_values():
    from bseed_z2m_postflash_verify import pm_readiness
    bad={'voltage':24065,'current':218,'power':28,'energy':156}
    issues=pm_readiness(pm_device(False),bad)
    assert any('max-60s reporting' in issue for issue in issues)
    assert any('PM voltage' in issue for issue in issues)
    assert any('PM current' in issue for issue in issues)

def test_pm_gate_accepts_configured_plausible_but_not_physical_acceptance():
    from bseed_z2m_postflash_verify import pm_readiness
    assert pm_readiness(pm_device(),{'voltage':240.65,'current':0.218,'power':28,'energy':0.156})==[]
    assert any('nonretained' in issue for issue in pm_readiness(pm_device(),None))
    assert any('max-60s' in issue for issue in pm_readiness(pm_device(False),{'voltage':240,'current':0,'power':0,'energy':0}))
