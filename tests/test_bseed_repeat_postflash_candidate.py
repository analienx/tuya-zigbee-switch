"""Repeated read-only postflash checks must not relax first-run interview authority."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_z2m_postota_reinterview import validate_campaign

IEEE="0xa4c1387b13e9436d"
NAME="WRSocketEntrance"
SHA="3a85623ab51e535b38384e41bedc13cf961721e36b62bb462419ce1b318b5f71"
T="bseed-ota-test"

def rec(phase):
    return dict(ieee=IEEE,device=NAME,sha256=SHA,phase=phase,token=T,
                response={"status":"ok","transaction":T,"data":{"id":IEEE}})

def test_same_successful_campaign_may_be_verified_again_without_reinterview():
    assert validate_campaign(rec("postflash_candidate"),IEEE,NAME,SHA,allow_postflash_candidate=True)
    with pytest.raises(ValueError,match="Exact campaign"):
        validate_campaign(rec("postflash_candidate"),IEEE,NAME,SHA)

@pytest.mark.parametrize("phase",["update_error","ota_running","update_timeout_or_unconfirmed","source_unchanged_abandoned","postflash_software_verified_network_released"])
def test_failed_or_terminal_campaign_may_not_be_verified(phase):
    with pytest.raises(ValueError,match="Exact campaign"):
        validate_campaign(rec(phase),IEEE,NAME,SHA,allow_postflash_candidate=True)

@pytest.mark.parametrize("field,val",[
    ("ieee","0xa4c1387b13e9436e"),("device","Other"),("sha256","0"*64),
    ("token","another-token"),
])
def test_mismatched_identity_or_transaction_remains_rejected(field,val):
    x=rec("postflash_candidate");x[field]=val
    with pytest.raises(ValueError):
        validate_campaign(x,IEEE,NAME,SHA,allow_postflash_candidate=True)

def test_transport_must_remain_confirmed():
    x=rec("postflash_candidate")
    x["response"]["status"]="error"
    with pytest.raises(ValueError,match="matched target"):
        validate_campaign(x,IEEE,NAME,SHA,allow_postflash_candidate=True)
