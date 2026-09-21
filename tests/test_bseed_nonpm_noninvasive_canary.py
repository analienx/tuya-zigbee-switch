"""Offline regression of the opt-in, exact-target non-invasive OTA route."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'helper_scripts'))
from bseed_nonpm_recovery_gate import verify_recovery
from bseed_ota_campaign import runner_args

SHA='92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd'
def canary():
    return dict(non_pm=True,require_pm=False,device='BedroomSocketCabinetRight',
        ieee='0xa4c13824a7005afb',manufacturer='o1jzcxou',model='TS011F-BS',
        preflash_role='EndDevice',postflash_role='EndDevice',
        preflash_build='1.1.2-bseedcli4',postflash_build='1.1.2-bseedcli5-rc1',
        sha256=SHA,block_bytes=32,relay_get_key='state_relay',expect_relay='OFF',
        preflash_relay_physical_mode='follow_state')

def test_only_pinned_unloaded_canary_may_opt_in_without_physical_readback():
    p=canary()
    with pytest.raises(ValueError,match='Physical load'):verify_recovery(p,accept_nonrecoverable_ota=True)
    with pytest.raises(ValueError,match='Recovery evidence'):verify_recovery(p,confirm_unloaded=True)
    d=verify_recovery(p,confirm_unloaded=True,accept_nonrecoverable_ota=True)
    assert d['recovery_available'] is False

def test_canary_opt_in_rejects_wrong_imei_role_image_and_relay():
    checks={'ieee':'0x0011223344556677','device':'OtherSocket','sha256':'b'*64,
        'preflash_build':'old','postflash_build':'new','block_bytes':50,
        'model':'TS011F-BS-PM','require_pm':True,'preflash_role':'Router',
        'postflash_role':'Router','relay_get_key':'state','expect_relay':'ON',
        'preflash_relay_physical_mode':'detached_on'}
    for key,value in checks.items():
        p=canary();p[key]=value
        with pytest.raises(ValueError):
            verify_recovery(p,confirm_unloaded=True,accept_nonrecoverable_ota=True)

def test_wrapper_passes_optin_to_lower_level_only_on_explicit_flash():
    p=canary();p.update({k:'test' for k in (
        'image','url','mqtt_config','broker','workdir','index_url')})
    p.update(manufacturer_code=4417,image_type=65026,file_version='285356048')
    cmd=runner_args(p,'flash',confirm_unloaded=True,accept_risk=True)
    assert cmd.count('--accept-nonrecoverable-ota-risk')==1
    assert cmd.count('--confirm-load-unplugged')==1
    assert '--accept-nonrecoverable-ota-risk' not in runner_args(p,'check')


def test_cli7_exact_hash_unloaded_canary_optin_and_all_neighboring_variants_denied():
    p=canary();p.update(postflash_build='1.1.2-bseedcli7',
        sha256='7726e53fb708eb154453bb5f03a18732ee92640acc675206405f3a307bcafedf')
    with pytest.raises(ValueError,match='Physical load'):
        verify_recovery(p,accept_nonrecoverable_ota=True)
    with pytest.raises(ValueError,match='Recovery evidence'):
        verify_recovery(p,confirm_unloaded=True)
    assert verify_recovery(p,confirm_unloaded=True,
        accept_nonrecoverable_ota=True)['recovery_available'] is False
    for key,value in {'sha256':'0'*64,'postflash_build':'1.1.2-bseedcli8',
        'preflash_build':'1.1.2-bseedcli5-rc1','ieee':'0x0011223344556677',
        'device':'BedroomSocketCabinetLeft','model':'TS011F-BS-PM',
        'preflash_role':'Router','postflash_role':'Router',
        'block_bytes':50,'require_pm':True,'relay_get_key':'state',
        'expect_relay':'ON','preflash_relay_physical_mode':'detached_on'}.items():
        altered=dict(p);altered[key]=value
        with pytest.raises(ValueError):
            verify_recovery(altered,confirm_unloaded=True,accept_nonrecoverable_ota=True)
