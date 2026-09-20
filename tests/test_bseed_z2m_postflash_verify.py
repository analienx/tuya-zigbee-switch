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
    assert 'expected role not verified' in issues

def test_old_build_must_not_pass_even_if_role_changes():
    verdict,issues,_=evaluate([record(build='old')],{'device':{'ieeeAddr':IEEE}},IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='unconfirmed' and 'expected firmware build not verified' in issues

def test_candidate_still_requires_independent_physical_acceptance():
    verdict,issues,_=evaluate([record()],{'device':{'ieeeAddr':IEEE},'state_relay':'ON'},IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='postflash_candidate' and issues==[]

def test_no_observed_state_is_not_accepted():
    verdict,issues,_=evaluate([record()],None,IEEE,'EndDevice','1.2.5-bseedcli6')
    assert verdict=='unconfirmed' and 'no live target state event after monitoring began' in issues
