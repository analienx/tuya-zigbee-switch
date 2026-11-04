"""Close an OTA *network lease* without falsely accepting offline new firmware."""
import copy,datetime as dt,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_defer_postflash_recovery import validate,SHA,CABINET,START,FINAL
from bseed_network_campaign_lock import ownership_diagnosis

TOKEN="bseed-ota-b8af575db3b5405d9829b7545790417e"

def case():
    p=dict(device="WorkroomSocketCabinet",ieee=CABINET,sha256=SHA,
       preflash_role="Router",postflash_role="EndDevice",
       preflash_build="1.2.5-bseedv8u5-rc5",postflash_build="1.2.5-bseedcli22",
       require_pm=True,pm_preflash_load_proof="physically_unloaded",
       network_id="0x00124b002d12b1fd")
    response=dict(status="ok",transaction=TOKEN,
       data={"id":CABINET,"from":{"file_version":302329874},
             "to":{"file_version":302329889}})
    w=dict(device=p["device"],ieee=CABINET,sha256=SHA,
       phase=START,token=TOKEN,completed="2026-11-04T13:14:49+01:00",
       response=response)
    n=dict(network_id=p["network_id"],device=p["device"],
       ieee=CABINET,image_sha256=SHA,phase=START,token=TOKEN)
    l=dict(bridge_state="online",permit_join=False,
       inventory=dict(ieee_address=CABINET,friendly_name=p["device"],
           interview_state="FAILED",software_build_id="1.2.5-bseedcli8"),
       device_ota_active=False,running_ota_runners=False)
    r=dict(ieee=CABINET,target=p["device"],result="unconfirmed",
       identity_verified=False,live_node_descriptor=None,
       close_response={"status":"ok"},observed_at="2026-11-04T13:16:53+01:00")
    g=dict(ieee=CABINET,sha256=SHA,token=TOKEN,phase="enabled",
       expected_role="EndDevice")
    s=dict(phase=START,update={"state":"idle","installed_version":302329889},
       response={"transaction":TOKEN})
    return p,w,n,l,r,g,s

NOW=dt.datetime.fromisoformat("2026-11-04T14:05:00+01:00")

def test_exact_completed_transport_defers_without_claiming_app_or_role():
    result=validate(*case(),now=NOW)
    assert result["result"]==FINAL
    assert result["transport_completed"] is True
    assert result["installed_image_verified"] is False
    assert result["current_zigbee_role_verified"] is False
    assert result["hardware_acceptance"] is False
    assert result["pm_telemetry_quarantine_retained"] is True
    assert result["new_ota_to_same_ieee_authorized"] is False
    assert ownership_diagnosis(dict(schema=1,phase=FINAL))["hardware_acceptance"]=="unverified"

@pytest.mark.parametrize("pos,key,value",[
    (0,"ieee","0x0123456789abcdef"),(0,"postflash_role","Router"),
    (0,"sha256","f"*64),(0,"pm_preflash_load_proof","meter"),
    (1,"phase","ota_running"),(1,"token","wrong"),
    (1,"completed","2026-11-04T14:02:00+01:00"),
    (2,"phase","postflash_candidate"),(2,"token","foreign"),
    (3,"bridge_state","offline"),(3,"permit_join",True),
    (3,"running_ota_runners",True),(3,"device_ota_active",True),
    (4,"result","confirmed"),(4,"identity_verified",True),
    (4,"observed_at","2026-11-04T13:00:00+01:00"),
    (5,"phase","released"),(5,"expected_role","Router"),
    (6,"phase","ota_running"),
])
def test_rejects_foreign_authority_live_activity_or_missing_recovery_proof(pos,key,value):
    args=list(copy.deepcopy(case()))
    args[pos][key]=value
    with pytest.raises((ValueError,TypeError)):
        validate(*args,now=NOW)

def test_transfer_ack_must_be_genuine_not_100_percent_progress_only():
    args=list(case())
    args[1]["response"]["data"]["to"]["file_version"]=302329874
    with pytest.raises(ValueError,match="Transport"):
        validate(*args,now=NOW)

def test_source_metadata_is_never_misrepresented_as_verified_client():
    args=list(case())
    args[3]["inventory"]["interview_state"]="SUCCESSFUL"
    with pytest.raises(ValueError,match="interview"):
        validate(*args,now=NOW)
