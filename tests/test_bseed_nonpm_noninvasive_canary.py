"""Offline regression of the exact-target non-invasive OTA route."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'helper_scripts'))
from bseed_nonpm_recovery_gate import verify_recovery, verify_transition_recovery
from bseed_ota_campaign import runner_args

SHA='92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd'
def canary():
    return dict(non_pm=True,require_pm=False,device='BedroomSocketCabinetRight',
        ieee='0xa4c13824a7005afb',manufacturer='o1jzcxou',model='TS011F-BS',
        preflash_role='EndDevice',postflash_role='EndDevice',
        preflash_build='1.1.2-bseedcli4',postflash_build='1.1.2-bseedcli5-rc1',
        sha256=SHA,block_bytes=32,relay_get_key='state_relay',expect_relay='OFF',
        preflash_relay_physical_mode='follow_state')

def test_only_pinned_unloaded_canary_may_run_without_physical_readback():
    p=canary()
    with pytest.raises(ValueError,match='Physical load'):verify_recovery(p)
    d=verify_recovery(p,confirm_unloaded=True)
    assert d['recovery_available'] is False

def test_canary_rejects_wrong_ieee_role_image_and_relay():
    checks={'ieee':'0x0011223344556677','device':'OtherSocket','sha256':'b'*64,
        'preflash_build':'old','postflash_build':'new','block_bytes':50,
        'model':'TS011F-BS-PM','require_pm':True,'preflash_role':'Router',
        'postflash_role':'Router','relay_get_key':'state','expect_relay':'ON',
        'preflash_relay_physical_mode':'detached_on'}
    for key,value in checks.items():
        p=canary();p[key]=value
        with pytest.raises(ValueError):
            verify_recovery(p,confirm_unloaded=True)

def test_wrapper_passes_load_confirmation_only_on_explicit_flash():
    p=canary();p.update({k:'test' for k in (
        'image','url','mqtt_config','broker','workdir','index_url')})
    p.update(manufacturer_code=4417,image_type=65026,file_version='285356048')
    cmd=runner_args(p,'flash',confirm_unloaded=True)
    assert cmd.count('--confirm-load-unplugged')==1
    assert '--confirm-load-unplugged' not in runner_args(p,'check')


def test_consolidated_c7_canary_uses_same_exact_device_gate():
    p=canary()
    p.update(postflash_build='1.1.3-bseedc7',
             sha256='f0a499ea9e351265cb47fa26717f00246450cecaf727adad3298552bb1d8a94f',
             expect_relay='ON')
    out=verify_recovery(p,confirm_unloaded=True)
    assert out['method']=='non-invasive exact Bedroom canary'
    bad=dict(p,sha256='0'*64)
    with pytest.raises(ValueError,match='exact signed-off'):
        verify_recovery(bad,confirm_unloaded=True)


def test_authorized_c7_to_c9_remains_exact_target_source_hash_and_role():
    p=canary()
    p.update(preflash_build='1.1.3-bseedc7', postflash_build='1.1.3-bseedc9',
             sha256='e52701b83ea7528ed0e9b26cb9e4da0679d125c259c8141e62207c6460738fff')
    assert verify_recovery(p,confirm_unloaded=True)['recovery_available'] is False
    with pytest.raises(ValueError,match='Physical load'):
        verify_recovery(p)
    for key,value in {'preflash_build':'1.1.3-bseedc6', 'sha256':'0'*64,
                      'postflash_build':'1.1.3-bseedc10', 'postflash_role':'Router',
                      'ieee':'0x0011223344556677', 'require_pm':True}.items():
        with pytest.raises(ValueError):
            verify_recovery(dict(p,**{key:value}),confirm_unloaded=True)


def test_consolidated_force_pair_requires_exact_native_destination():
    p=canary()
    p.update(preflash_build='1.1.3-bseedc7',postflash_build='1.1.3-bseedr10',
             preflash_role='EndDevice',postflash_role='Router',expect_relay='ON',
             force_test_transition=True,
             native_sha256='c2bb21dee350fd375586029eefb03f85791b0941bd386882a0b8653bb15bdb96',
             sha256='a'*64)
    out=verify_transition_recovery(p,confirm_unloaded=True)
    assert out['recovery_available'] is False
    with pytest.raises(ValueError,match='exact signed-off'):
        verify_transition_recovery(dict(p,native_sha256='0'*64), confirm_unloaded=True)
    with pytest.raises(ValueError,match='exact signed-off'):
        verify_transition_recovery(dict(p,force_test_transition=False), confirm_unloaded=True)

    back=dict(p,preflash_build='1.1.3-bseedr10',postflash_build='1.1.3-bseedc7',
              preflash_role='Router',postflash_role='EndDevice',
              native_sha256='f0a499ea9e351265cb47fa26717f00246450cecaf727adad3298552bb1d8a94f')
    assert verify_transition_recovery(back,confirm_unloaded=True)['recovery_available'] is False
