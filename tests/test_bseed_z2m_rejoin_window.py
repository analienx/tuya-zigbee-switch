"""Offline validation of bounded, router-scoped postflash rejoin; never opens join."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_z2m_rejoin_window import validate_recovery

IEEE='0xa4c138241e3de538'
INVENTORY=[{'friendly_name':'KitchenSocketLeft','ieee_address':IEEE,'type':'Router','interview_completed':False},
           {'friendly_name':'KitchenSocketRight','ieee_address':'0xa4c138075cd16ed4','type':'Router','interview_completed':True}]
LOCK={'ieee':IEEE,'phase':'ota_transfer_ok_postflash_unverified'}

def test_exact_stale_target_and_distinct_online_router_allowed():
    target,router=validate_recovery(IEEE,IEEE,'KitchenSocketRight',INVENTORY,False,LOCK)
    assert target['ieee_address']==IEEE and router['friendly_name']=='KitchenSocketRight'

@pytest.mark.parametrize('confirmation,join,phase,via',[
    ('0xBAD',False,'ota_transfer_ok_postflash_unverified','KitchenSocketRight'),
    (IEEE,True,'ota_transfer_ok_postflash_unverified','KitchenSocketRight'),
    (IEEE,False,'update_error','KitchenSocketRight'),
    (IEEE,False,'ota_transfer_ok_postflash_unverified','KitchenSocketLeft'),
    (IEEE,False,'ota_transfer_ok_postflash_unverified','MissingRouter')])
def test_unsafe_recovery_state_is_blocked(confirmation,join,phase,via):
    with pytest.raises(ValueError):
        validate_recovery(IEEE,confirmation,via,INVENTORY,join,{'ieee':IEEE,'phase':phase})
