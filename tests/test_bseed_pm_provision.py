"""Offline PM provisioning unit tests."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_pm_provision import select_target, missing_reports, inspect_db, validate_states, REPORTS

IEEE='0x0011223344556677'
COORD='0xaabbccddeeff1122'
def device(reportings=None):
    return {'ieee_address':IEEE,'friendly_name':'canary','type':'EndDevice',
            'software_build_id':'cli6','interview_completed':True,
            'endpoints':{'1':{'clusters':{'input':['haElectricalMeasurement','seMetering']},
                            'configured_reportings': reportings or []},'2':{}}}

def reporting(row):
    cluster, name, attr, low, high, change=row
    return {'cluster':{'haElectricalMeasurement':2820,'seMetering':1794}[cluster],
            'attrId':attr,'minRepIntval':low,'maxRepIntval':high,'repChange':change}

def database():
    return {'ieeeAddr':IEEE,'swBuildId':'cli6', 'endpoints':{'1':{
      'clusters':{'haElectricalMeasurement':{'attributes':{
        'rmsVoltage':24075,'rmsCurrent':41,'activePower':0,
        'acVoltageMultiplier':1,'acVoltageDivisor':100,'acCurrentMultiplier':1,
        'acCurrentDivisor':1000,'acPowerMultiplier':1,'acPowerDivisor':1}},
        'seMetering':{'attributes':{'currentSummDelivered':165,'multiplier':1,'divisor':1000}}},
      'binds':[{'cluster':c,'type':'endpoint','deviceIeeeAddress':COORD,'endpointID':1}
                for c in (2820,1794)]}}}

def test_exact_identity_and_endpoints():
    assert select_target([device()],IEEE,'canary','EndDevice','cli6')['type']=='EndDevice'
    with pytest.raises(ValueError):select_target([device()],IEEE,'other','EndDevice','cli6')
    with pytest.raises(ValueError):select_target([device(),device()],IEEE,'canary','EndDevice','cli6')

def test_idempotent_reporting_and_missing_power():
    all_good=device([reporting(row) for row in REPORTS])
    assert missing_reports(all_good)==[]
    assert missing_reports(device([reporting(row) for row in REPORTS[1:]]))[0][1]=='activePower'
    all_good['endpoints']['1']['configured_reportings'][0]['maxRepIntval']=3600
    assert missing_reports(all_good)[0][1]=='activePower'

def test_scaling_and_binding_fail_closed():
    db=database(); assert inspect_db(db,IEEE,'cli6',COORD)['cached_scales_verified']
    db['endpoints']['1']['clusters']['seMetering']['attributes']['divisor']=0
    with pytest.raises(ValueError,match='scale'):inspect_db(db,IEEE,'cli6',COORD)

def test_wrong_coordinator_binding_cannot_pass():
    db=database(); db['endpoints']['1']['binds'][0]['deviceIeeeAddress']=IEEE
    with pytest.raises(ValueError,match='binding'):inspect_db(db,IEEE,'cli6',COORD)

def test_detect_unscaled_energy_and_true_idle():
    db=database(); good={'voltage':240.75,'current':0,'power':0,'energy':.165}
    assert validate_states([(1.0,good),(70.0,dict(good))],db,True)['idle_zero_confirmed']
    with pytest.raises(ValueError,match='scaling'):
        validate_states([(1.0,good),(70.0,dict(good,energy=165))],db)
    with pytest.raises(ValueError,match='no-load'):
        validate_states([(1.0,good),(70.0,dict(good,current=.04,power=1))],db,True)

def test_energy_monotonic_and_freshness():
    db=database(); a={'voltage':240.75,'current':.041,'power':0,'energy':.17}
    with pytest.raises(ValueError,match='two separated'):
        validate_states([(1.0,a),(1.5,a)],db)
    with pytest.raises(ValueError,match='decreased'):
        validate_states([(1.0,a),(70.0,dict(a,energy=.15))],db)


def test_idle_observation_covers_full_current_reporting_window():
    from bseed_pm_provision import observation_policy
    assert observation_policy(135, False) == (135, 8)
    assert observation_policy(135, True) == (330, 310)
    assert observation_policy(400, True) == (400, 310)
    with pytest.raises(ValueError, match='bounded'):
        observation_policy(500, True)
