"""Exact-source software-successor and no-readback risk gates (GitHub CI only)."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import pytest
from bseed_nonpm_recovery_gate import is_exact_balcony_c19_operator_risk, verify_recovery
from bseed_balcony_software_successor import proven
from bseed_canary import find_campaign_locks

IEEE="0xa4c138ac993cc245"
SHA="a551668210ef91d9b43788e1a2dc281f691a6ca47b016b9b7f4a68cc1449ad63"


def profile():
    return dict(device="BedroomSocketBalcony",ieee=IEEE,manufacturer="o1jzcxou",
        model="TS011F-BS",preflash_role="EndDevice",postflash_role="EndDevice",
        preflash_build="1.1.3-bseedc17",postflash_build="1.1.3-bseedc19",
        sha256=SHA,preflash_relay_physical_mode="follow_state",expect_relay="OFF",
        relay_get_key="state_relay",require_pm=False,non_pm=True,
        force_test_transition=False,auto_relay_off=False,network_lock_shared=True,
        parallel_ota_max=3,block_bytes=48,response_delay_ms=1800,
        source_rejoin_strategy="none",allow_join_all_fallback=False,
        owner_irreversible_risk_accepted=True)


def test_exact_risk_requires_physical_unplugged_and_pinned_candidate():
    p=profile()
    assert is_exact_balcony_c19_operator_risk(p,("EndDevice","EndDevice"),False)
    result=verify_recovery(p,confirm_unloaded=True)
    assert result["readback_verified"] is False
    assert result["hardware_acceptance"] is False
    with pytest.raises(ValueError,match="Physical load"):
        verify_recovery(p,confirm_unloaded=False)
    for key,value in (
        ("ieee","0x0123456789abcdef"),("sha256","0"*64),
        ("auto_relay_off",True),("expect_relay","ON"),("block_bytes",32),
        ("source_rejoin_strategy","auto"),("owner_irreversible_risk_accepted",False),
        ("parallel_ota_max",5),("postflash_role","Router"),
        ("response_delay_ms",1200)):
        changed={**p,key:value}
        assert not is_exact_balcony_c19_operator_risk(
            changed,("EndDevice","EndDevice"),False),key


def put(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,indent=2)+"\n",encoding="utf8")
    return p


def evidence(tmp_path):
    now=dt.datetime(2026,11,17,10,0,tzinfo=dt.timezone.utc)
    old_path=tmp_path/"old"/"ACTIVE_LOCK.json"
    new_path=tmp_path/"new"/"ACTIVE_LOCK.json"
    post_path=tmp_path/"new"/"postflash.json"
    release_path=tmp_path/"new"/"software.json"
    old=dict(device="BedroomSocketBalcony",ieee=IEEE,
        token="bseed-ota-7b842b26e5db486b82dbec31fe5dbea8",
        sha256="347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe",
        phase="interrupted_source_identity_mismatch_operator_isolated_network_released",
        hardware_acceptance=False,same_ieee_new_ota_authorized=False,
        started="2026-11-15T11:53:20+01:00",
        network_lock=str(tmp_path/"no-longer-leased.json"))
    put(old_path,old)
    succ=dict(device="BedroomSocketBalcony",ieee=IEEE,
        phase="postflash_software_verified_network_released",
        sha256="3ae273707a657b79a8bd2b7f6f77794d57547ba3a52247627b4a7c335f932b08",
        token="bseed-ota-a559956d42c24170959c4d858c7c9ff4",
        started="2026-11-16T12:17:49+01:00",
        completed="2026-11-16T12:57:53+01:00",
        response=dict(status="ok",transaction="bseed-ota-a559956d42c24170959c4d858c7c9ff4",
            data=dict(id=IEEE,to=dict(software_build_id="1.1.3-bseedc17",file_version=285356062))))
    put(new_path,succ)
    post=dict(result="postflash_candidate",issues=[],errors=[],
        target_name="BedroomSocketBalcony",target_ieee=IEEE,
        expected_build="1.1.3-bseedc17",live_node_descriptor=dict(role="EndDevice"),
        state=dict(state_relay="OFF"))
    put(post_path,post)
    release=dict(device="BedroomSocketBalcony",ieee=IEEE,
        token=succ["token"],sha256=succ["sha256"],
        installed_build="1.1.3-bseedc17",installed_role="EndDevice",
        result="postflash_software_verified_network_released",
        softwareVerification=True,networkOwnershipReleased=True,hardwareAcceptance=False,
        at="2026-11-16T13:12:11+00:00",
        evidence={post_path.name:dict(path=str(post_path),sha256=hashlib.sha256(post_path.read_bytes()).hexdigest())})
    put(release_path,release)
    config=dict(old_lock=str(old_path),successor_lock=str(new_path),
        software_evidence=str(release_path),postflash_evidence=str(post_path),
        authorize_new_same_role_ota=True)
    live=dict(ieeeAddr=IEEE,swBuildId="1.1.3-bseedc17",type="EndDevice",
        manufName="o1jzcxou",modelId="TS011F-BS",interviewCompleted=True,
        lastSeen=(now-dt.timedelta(seconds=10)).timestamp()*1000)
    return old_path,old,config,live,now


def test_software_successor_recognized_without_changing_old_failure(tmp_path):
    path,old,config,live,now=evidence(tmp_path)
    assert proven(config,old=old,old_path=path,live=live,now=now)
    assert json.loads(path.read_text(encoding="utf8"))["phase"] == old["phase"]
    # find_campaign_locks checks genuine current wall time, unlike the fixed
    # historical fixture used to exercise the pure successor validator above.
    live["lastSeen"] = dt.datetime.now(dt.timezone.utc).timestamp()*1000
    site={"campaign_root":str(tmp_path),"lock_search_roots":[],
        "devices":{"BedroomSocketBalcony":dict(
            ieee=IEEE,board="nonpm",role="EndDevice",expect_relay="OFF",
            balcony_software_successor=config)}}
    assert all(x["clean"] for x in find_campaign_locks(site,"BedroomSocketBalcony",live=live))


def test_successor_refuses_stale_or_foreign_or_unverified(tmp_path):
    path,old,config,live,now=evidence(tmp_path)
    bad={**live,"lastSeen":(now-dt.timedelta(minutes=30)).timestamp()*1000}
    with pytest.raises(ValueError,match="stale"):
        proven(config,old=old,old_path=path,live=bad,now=now)
    with pytest.raises(ValueError,match="Zigbee source"):
        proven(config,old=old,old_path=path,
               live={**live,"swBuildId":"1.1.3-bseedc16"},now=now)
    release=Path(config["software_evidence"])
    item=json.loads(release.read_text(encoding="utf8"))
    item["hardwareAcceptance"]=True
    put(release,item)
    with pytest.raises(ValueError,match="software-release"):
        proven(config,old=old,old_path=path,live=live,now=now)
