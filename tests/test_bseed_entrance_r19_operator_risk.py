
import copy
import pytest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_nonpm_recovery_gate import verify_recovery

BASE = {
 'device':'WRSocketEntrance','ieee':'0xa4c1387b13e9436d',
 'manufacturer':'o1jzcxou','model':'TS011F-BS','preflash_role':'Router','postflash_role':'Router',
 'preflash_build':'1.1.3-bseedr18','postflash_build':'1.1.3-bseedr19',
 'sha256':'3a85623ab51e535b38384e41bedc13cf961721e36b62bb462419ce1b318b5f71',
 'parallel_ota_max':2,'concurrent_ota_ieees':['0xa4c138c5f07ee732'],
 'source_rejoin_strategy':'none','block_bytes':32,'response_delay_ms':1800,
 'preflash_relay_physical_mode':'follow_state','expect_relay':'OFF',
 'relay_get_key':'state_relay','require_pm':False,'non_pm':True,'force_test_transition':False,
 'recovery_evidence':None,
}

def test_only_exact_operator_canary_may_skip_readback_when_unloaded():
    r=verify_recovery(BASE,confirm_unloaded=True)
    assert r['recovery_available'] is False and r['readback_verified'] is False
    assert r['operator_risk_accepted'] is True
    with pytest.raises(ValueError,match='Physical load'):
        verify_recovery(BASE,confirm_unloaded=False)

@pytest.mark.parametrize('field,value',[
 ('device','OtherSocket'),('ieee','0xa4c1387b13e9436e'),
 ('preflash_build','1.1.3-bseedr17'),('postflash_build','1.1.3-bseedr20'),
 ('sha256','0'*64),('parallel_ota_max',1),('concurrent_ota_ieees',['0xa4c138c5f07ee731']),
 ('source_rejoin_strategy','auto'),('block_bytes',64),('response_delay_ms',1200),
 ('preflash_relay_physical_mode','power_on'),('expect_relay','ON'),
 ('relay_get_key','state'),('require_pm',True),('force_test_transition',True),
 ('preflash_role','EndDevice'),('postflash_role','EndDevice'),
])
def test_other_ota_is_not_authorized(field,value):
    s=copy.deepcopy(BASE);s[field]=value
    with pytest.raises((ValueError,TypeError)):
        verify_recovery(s,confirm_unloaded=True)
