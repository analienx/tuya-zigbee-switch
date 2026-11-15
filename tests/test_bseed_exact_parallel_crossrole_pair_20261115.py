"""Specific 2026-11-15 user opt-in two-device role transition admission."""
import hashlib
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_network_campaign_lock import (
  acquire, require_profile_authority, read_lock, update,
  EXACT_ROLE_CHANGE_PAIR, EXACT_ROLE_CHANGE_PAIR_ID,
)

BAL="0xa4c138ac993cc245"
DOOR="0xa4c1386e4892f726"
NET="0x00124b002d12b1fd"

def pair_profile(tmp_path, ieee):
    other=DOOR if ieee==BAL else BAL
    q=EXACT_ROLE_CHANGE_PAIR[ieee]
    return {
      "device":q["device"],"ieee":ieee,"sha256":q["sha256"],
      "preflash_role":q["preflash_role"],"postflash_role":q["postflash_role"],
      "preflash_build":q["preflash_build"],"postflash_build":q["postflash_build"],
      "force_test_transition":False,
      "network_lock_dir":str(tmp_path/"authority"),
      "network_id":NET,"network_lock_shared":True,
      "workdir":str(tmp_path/("work"+ieee[-3:])),
      "parallel_ota_max":2,"concurrent_ota_ieees":[other],
      "parallel_role_change_pair":EXACT_ROLE_CHANGE_PAIR_ID,
      "source_rejoin_strategy":"scoped",
      "join_via":"BedroomSocketCabinetL","join_seconds":120,
      "allow_join_all_fallback":False,"block_bytes":32,"response_delay_ms":1800,
    }

def claim(p):
    return acquire(require_profile_authority(p),network_id=NET,token=p["ieee"],
        device=p["device"],ieee=p["ieee"],image_sha256=p["sha256"],
        parallel_allowed_ieees=p["concurrent_ota_ieees"],
        role_change_pair=p["parallel_role_change_pair"])

def test_two_pinned_role_changing_devices_coexist(tmp_path):
    bal,door=pair_profile(tmp_path,BAL),pair_profile(tmp_path,DOOR)
    a=claim(bal)
    assert a["source_role"]=="EndDevice" and a["target_role"]=="Router"
    update(require_profile_authority(bal),BAL,"ota_running")
    b=claim(door)
    assert b["source_role"]=="Router" and b["target_role"]=="EndDevice"
    assert read_lock(require_profile_authority(bal))["phase"]=="ota_running"
    assert read_lock(require_profile_authority(door))["phase"]=="preflight_complete"

def test_wrong_source_role_image_or_peer_fails_before_acquisition(tmp_path):
    for i,(key,bad) in enumerate((
      ("sha256","0"*64),("preflash_role","Router"),
      ("postflash_role","EndDevice"),("source_rejoin_strategy","none"),
      ("join_via","BedroomSocketCabinetRight"),
      ("concurrent_ota_ieees",["0xa4c13800000000ff"]),
      ("join_seconds",600),("allow_join_all_fallback",True),
      ("parallel_role_change_pair","unrestricted"),
      ("force_test_transition",True),
      ("response_delay_ms",1200),
    )):
        p=pair_profile(tmp_path/str(i),BAL)
        p[key]=bad
        with pytest.raises(ValueError):
            require_profile_authority(p)

def test_two_slot_requires_existing_peer_exact_role_change_optin(tmp_path):
    first=pair_profile(tmp_path,BAL)
    claim(first)
    update(require_profile_authority(first),BAL,"ota_running")
    second=pair_profile(tmp_path,DOOR)
    with pytest.raises(RuntimeError):
        acquire(require_profile_authority(second),network_id=NET,token=DOOR,
         device=second["device"],ieee=DOOR,image_sha256=second["sha256"],
         parallel_allowed_ieees=[BAL])
    with pytest.raises(ValueError):
        acquire(require_profile_authority(second),network_id=NET,token=DOOR,
         device=second["device"],ieee=DOOR,image_sha256="f"*64,
         parallel_allowed_ieees=[BAL],role_change_pair=EXACT_ROLE_CHANGE_PAIR_ID)

def test_third_target_cannot_use_exact_pair_override(tmp_path):
    p=pair_profile(tmp_path,BAL)
    p["ieee"]="0xa4c13800000000fe"
    with pytest.raises(ValueError):
        require_profile_authority(p)

def test_existing_singleton_lease_not_migrated_in_place(tmp_path):
    from bseed_network_campaign_lock import authority_path
    p=pair_profile(tmp_path,BAL)
    singleton=authority_path(p["network_lock_dir"],NET)
    acquire(singleton,network_id=NET,token="previous-owner",device="BedroomSocketBalcony",
            ieee=BAL,image_sha256=EXACT_ROLE_CHANGE_PAIR[BAL]["sha256"])
    with pytest.raises(RuntimeError,match="Legacy network OTA ownership"):
        claim(p)
    assert read_lock(singleton)["token"]=="previous-owner"
