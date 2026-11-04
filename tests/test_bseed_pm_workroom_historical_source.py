"""Workroom rc5 Router stale Basic workaround is exact, independently live-verified."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_pm_historical_workroom_source import (
    SOURCE,is_exact_historical_workroom_profile,
    is_expected_stale_inventory,require_fresh_historical_source,
)


def identity():
    return dict(SOURCE)


def inventory():
    return dict(friendly_name="WorkroomSocketCabinet",
       ieee_address="0xa4c138c5f07ee732",manufacturer="b28wrpvx",
       model_id="TS011F-BS-PM",type="Router",
       software_build_id="1.2.5-bseedcli8",
       network_address=53508,interview_state="SUCCESSFUL")


def relay():
    return dict(device=dict(ieeeAddr="0xa4c138c5f07ee732",
            manufacturerName="b28wrpvx",type="Router",
            softwareBuildID="1.2.5-bseedcli8",dateCode="20260926"),
        update=dict(installed_version=302329874,state="idle"),
        state_relay="ON")


def zdo():
    return dict(role="Router",logical_type=1,nwk_address=53508,
        manufacturer_code=4417)


def test_live_stale_cache_requires_source_rc5_zdo_router():
    p=identity();i=inventory()
    assert is_exact_historical_workroom_profile(p)
    assert is_expected_stale_inventory(p,i)
    evidence=require_fresh_historical_source(p,i,relay(),zdo())
    assert evidence["source_build"]=="1.2.5-bseedv8u5-rc5"
    assert evidence["identity_evidence_is_not_direct_readback"] is True
    assert evidence["physical_load_proof"]=="requires operator confirmation at flash"


@pytest.mark.parametrize("field,value",[
    ("device","KitchenSocketLeft"),("ieee","0xa4c138241e3de538"),
    ("manufacturer","o1jzcxou"),("model","TS011F-BS"),
    ("preflash_build","1.2.5-bseedcli8"),("preflash_cached_build","rc5"),
    ("preflash_version",302329872),("preflash_role","EndDevice"),
    ("postflash_build","1.2.5-bseedr19"),("postflash_role","Router"),
    ("sha256","0"*64),("native_sha256","f"*64),("image_type",65024),
    ("file_version","0xffffffff"),("force_test_transition",True),
    ("pm_preflash_load_proof","meter"),("join_via","BedroomSocketBalcony"),
    ("require_pm",False),("non_pm",True),("relay_get_key","state"),
    ("expect_relay","OFF")
])
def test_only_exact_fleet_workroom_image_may_alias_stale_build(field,value):
    p=identity();p[field]=value
    assert not is_exact_historical_workroom_profile(p)
    assert not is_expected_stale_inventory(p,inventory())
    with pytest.raises(ValueError,match="not authorized"):
        require_fresh_historical_source(p,inventory(),relay(),zdo())


@pytest.mark.parametrize("field,value",[
    ("friendly_name","KitchenSocketRight"),("ieee_address","0x0123456789abcdef"),
    ("manufacturer","foo"),("model_id","TS011F"),
    ("type","EndDevice"),("software_build_id","1.2.5-bseedv8u5-rc5"),
    ("network_address",None),("interview_state","FAILED")
])
def test_stale_metadata_never_allowed_on_wrong_target(field,value):
    i=inventory();i[field]=value
    assert not is_expected_stale_inventory(identity(),i)


@pytest.mark.parametrize("path,value",[
    (("device","type"),"EndDevice"),(("device","ieeeAddr"),"0xa4c138241e3de538"),
    (("device","manufacturerName"),"o1jzcxou"),
    (("device","softwareBuildID"),"1.2.5-bseedcli22"),
    (("device","dateCode"),"20261001"),
    (("update","installed_version"),302329872),
    (("update","state"),"updating"),
    (("state_relay",),"OFF"),
])
def test_live_source_must_remain_exact(path,value):
    s=relay()
    obj=s
    for key in path[:-1]:obj=obj[key]
    obj[path[-1]]=value
    with pytest.raises(ValueError):
        require_fresh_historical_source(identity(),inventory(),s,zdo())


@pytest.mark.parametrize("field,value",[
    ("role","EndDevice"),("logical_type",2),("nwk_address",19201),
    ("manufacturer_code",1234)
])
def test_fresh_zdo_is_mandatory_router_proof(field,value):
    z=zdo();z[field]=value
    with pytest.raises(ValueError,match="fresh ZDO"):
        require_fresh_historical_source(identity(),inventory(),relay(),z)
