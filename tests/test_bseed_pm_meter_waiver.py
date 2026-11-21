"""Operator PM freshness waiver is narrow and never turns off a relay."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
import bseed_pm_meter_waiver as w
from bseed_ota_campaign import runner_args

def profile(name="LivingRoomSocketWifiLeft",role="Router",**changes):
    x={
        "device":name,
        "ieee":w.APPROVED[name][0],
        "preflash_role":role,
        "postflash_role":role,
        "require_pm":True,
        "non_pm":False,
        "force_test_transition":False,
        "relay_state_policy":"preserve",
        "auto_relay_off":False,
        "relay_get_key":"state_relay",
        "pm_preflash_load_proof":"meter",
        "operator_waive_preflash_pm":True,
    }
    x.update(changes)
    return x

@pytest.mark.parametrize("name,role",[
    ("LivingRoomSocketWifiLeft","Router"),
    ("KitchenSocketLeft","EndDevice"),
    ("KitchenSocketRight","Router"),
])
def test_waiver_only_exact_three(name,role):
    assert w.validate(profile(name,role)) is True

@pytest.mark.parametrize("changes",[
    {"ieee":"0xa4c138aed67ea773"},
    {"device":"LivingRoomSocketHA"},
    {"preflash_role":"EndDevice"},
    {"postflash_role":"EndDevice"},
    {"require_pm":False},
    {"non_pm":True},
    {"auto_relay_off":True},
    {"relay_state_policy":None},
    {"relay_get_key":"state"},
    {"pm_preflash_load_proof":"physically_unloaded"},
    {"force_test_transition":True},
    {"operator_waive_preflash_pm":"true"},
])
def test_waiver_fail_closed(changes):
    with pytest.raises(ValueError):
        w.validate(profile(**changes))

def test_legacy_default_is_not_waived():
    assert w.validate({}) is False
    assert w.validate({"operator_waive_preflash_pm":False}) is False

def test_runner_passes_flag_only_for_approved_profile():
    import bseed_ota_campaign as campaign
    # Runner argument rendering does not perform IO, and retains all explicit flags.
    p=profile()
    for k in ("ieee","device"): assert p[k]
    p.update(manufacturer="b28wrpvx",model="TS011F-BS-PM", image="a",
             sha256="0"*64,url="http://example.invalid/a",mqtt_config="x",
             broker="x",workdir="x",manufacturer_code=4417,image_type=43556,
             file_version="0x12053026",expect_relay="ON",index_url="http://example.invalid/index")
    assert "--waive-preflash-pm" in runner_args(p,"flash")
    p["operator_waive_preflash_pm"]=False
    assert "--waive-preflash-pm" not in runner_args(p,"flash")
