"""Offline tests: Router must never receive a Client scale repair."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_pm_role_audit import (assess_samples,check_persisted,settings_snapshot,
                                 settings_drift)
from test_bseed_pm_provision import IEEE,COORD,database

def router_db():
    d=database();d['swBuildId']='1.2.5-bseedv8u4';d['type']='Router'
    e=d['endpoints']['1']['clusters']['haElectricalMeasurement']['attributes']
    for name in ('acVoltageMultiplier','acVoltageDivisor','acPowerMultiplier','acPowerDivisor'):
        e.pop(name,None)
    e['rmsVoltage']=238
    m=d['endpoints']['1']['clusters']['seMetering']['attributes']
    m['divisor']=100;m['currentSummDelivered']=1172
    d['endpoints']['2']={'clusters':{'genOnOff':{'attributes':{'onOff':1,'startUpOnOff':2}}}}
    d['endpoints']['1']['clusters']['manuSpecificTuya3']={'attributes':{'powerOnBehavior':2}}
    return d

def test_router_scale_does_not_require_client_divisors():
    d=router_db()
    assert check_persisted(d,IEEE,'1.2.5-bseedv8u4','Router',COORD)
    with pytest.raises(ValueError,match='role'):
        check_persisted(d,IEEE,'1.2.5-bseedv8u4','EndDevice',COORD)
    d['type']='EndDevice'
    with pytest.raises(ValueError,match='Unverified scale'):
        check_persisted(d,IEEE,'1.2.5-bseedv8u4','EndDevice',COORD)

def test_router_invalid_energy_divisor_and_missing_bind_fail_closed():
    d=router_db();d['endpoints']['1']['clusters']['seMetering']['attributes']['divisor']=0
    with pytest.raises(ValueError,match='scale'):check_persisted(d,IEEE,d['swBuildId'],'Router',COORD)
    d=router_db();d['endpoints']['1']['binds'][0]['deviceIeeeAddress']=IEEE
    with pytest.raises(ValueError,match='bindings'):check_persisted(d,IEEE,d['swBuildId'],'Router',COORD)

def test_settings_snapshot_ignores_live_relay_but_preserves_restart_setting():
    d=router_db();s=settings_snapshot(d)
    assert s['2/genOnOff']=={'startUpOnOff':2}
    assert s['1/manuSpecificTuya3']=={'powerOnBehavior':2}
    d['endpoints']['2']['clusters']['genOnOff']['attributes']['onOff']=0
    assert settings_snapshot(d)==s
    d['endpoints']['2']['clusters']['genOnOff']['attributes']['startUpOnOff']=1
    assert settings_drift(s,settings_snapshot(d))
    assert settings_drift(s,s)=={}

def test_router_passive_scaled_energy_and_uncertainty():
    d=router_db();attrs=d['endpoints']['1']['clusters']
    src={'voltage':238.,'current':0.,'power':0.,'energy':11.72}
    result=assess_samples([(1.,src),(70.,dict(src))],{k:v['attributes'] for k,v in attrs.items() if k in ('haElectricalMeasurement','seMetering')},'Router')
    assert result['messages']==2 and not result['router_voltage_scale_independently_verified']
    assert not result['raw_zcl_report_proven']

def test_router_unscaled_energy_fails_and_no_invalid_idle_inference():
    d=router_db();a={k:v['attributes'] for k,v in d['endpoints']['1']['clusters'].items() if k in ('haElectricalMeasurement','seMetering')}
    s={'voltage':238.,'current':0.,'power':0.,'energy':1172.}
    with pytest.raises(ValueError,match='energy discrepancy'):
        assess_samples([(1.,s),(70.,dict(s))],a,'Router')
    with pytest.raises(ValueError,match='Insufficient'):
        assess_samples([(1.,s),(3.,dict(s))],a,'Router')

def test_wrong_role_build_and_missing_baseline_do_not_get_promoted():
    d=router_db();d['type']='EndDevice'
    with pytest.raises(ValueError,match='role'):
        check_persisted(d,IEEE,d['swBuildId'],'Router',COORD)
    with pytest.raises(ValueError,match='Baseline'):
        settings_drift([],settings_snapshot(d))
