"""No-live-device tests: same-role OTA must preserve its selected relay state and PM energy."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_z2m_postflash_verify import assess_retained_state


def test_router_reports_divergent_logical_and_physical_relay_state():
    before={'state':'ON','state_relay':'ON','energy':11.72,
            'relay_physical_mode':'follow_state'}
    after={'state':'ON','state_relay':'OFF','energy':0,
           'relay_physical_mode':'follow_state'}
    issues=assess_retained_state(before,after,'state_relay',True)
    assert any('Relay state changed' in x for x in issues)
    assert any('energy dropped' in x for x in issues)
    issues=assess_retained_state(before,dict(before,energy=11.74),'state_relay',True)
    assert any('energy continuity unverified' in x for x in issues)


def test_matching_cached_energy_cannot_prove_retention():
    # A fresh relay message can carry the old energy after the meter resets.
    before={'state_relay':'ON','energy':2.95,'relay_physical_mode':'follow_state'}
    after=dict(before)
    assert assess_retained_state(before,after,'state_relay',True) == [
        'PM energy continuity unverified: device-originated pre/post readings and scales required']
    assert not assess_retained_state(before,after,'state_relay',False)


def test_absent_baseline_meter_and_policy_drift_fail_closed():
    assert assess_retained_state(None,{},'state_relay',True)
    before={'state_relay':'ON','relay_physical_mode':'always_on','energy':2.5}
    after={'state_relay':'ON','relay_physical_mode':'follow_state','energy':None}
    issues=assess_retained_state(before,after,'state_relay',True)
    assert any('policy changed' in x for x in issues)
    assert any('Missing/invalid' in x for x in issues)
    assert any('Preflash relay state missing' in x for x in
               assess_retained_state({},dict(before),'state_relay',False))
