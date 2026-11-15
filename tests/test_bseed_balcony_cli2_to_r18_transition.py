"""Portable unit tests for one-device legacy Client->Router recovery waiver."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"helper_scripts"))
import bseed_balcony_cli2_to_r18_transition as mod
from bseed_nonpm_recovery_gate import verify_transition_recovery
from bseed_nonpm_relay_policy import relay_policy_matches


def write(path, obj):
    path.write_text(json.dumps(obj),encoding="utf8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def evidence(tmp_path,monkeypatch):
    native=bytearray(b"\x00"*120)
    struct.pack_into("<HHI",native,10,4417,43555,mod.DST_VERSION)
    wrapper=bytearray(native)
    struct.pack_into("<H",wrapper,12,65026)
    native_path=tmp_path/"native.ota";native_path.write_bytes(native)
    wrapper_path=tmp_path/"transport.ota";wrapper_path.write_bytes(wrapper)
    monkeypatch.setattr(mod,"NATIVE_SHA",hashlib.sha256(native).hexdigest())
    monkeypatch.setattr(mod,"WRAPPER_SHA",hashlib.sha256(wrapper).hexdigest())
    ts=datetime.now(timezone.utc).isoformat()
    meta={"at":ts,"device":"BedroomSocketBalcony","ieee":mod.IEEE,
          "result":"metadata_refreshed","interview_ok":True,
          "fresh_inventory_observed":True,"inventory_role_after":"EndDevice",
          "live_zdo_after":{"role":"EndDevice"},
          "after":{"software_build_id":"1.1.2-bseedcli2","type":"EndDevice"}}
    ota={"at":ts,"status":"availability_only","update_command_sent":False,
          "inventory":[{"ieee":mod.IEEE,"role":"EndDevice","build":"1.1.2-bseedcli2"}],
          "response":{"status":"ok","data":{"id":mod.IEEE,"update_available":True,
               "source":"https://example.org/transport.ota"}},
          "updates":[[ts,{"installed_version":mod.SRC_VERSION,
                             "latest_version":mod.DST_VERSION}]]}
    mf=tmp_path/"metadata.json";msha=write(mf,meta)
    of=tmp_path/"check.json";osha=write(of,ota)
    risk={"schema":1,"at":ts,"device":"BedroomSocketBalcony","ieee":mod.IEEE,
          "source_build":"1.1.2-bseedcli2","source_role":"EndDevice",
          "source_file_version":mod.SRC_VERSION,
          "target_build":"1.1.3-bseedr18","target_role":"Router",
          "target_file_version":mod.DST_VERSION,
          "transport_sha256":mod.WRAPPER_SHA,"native_sha256":mod.NATIVE_SHA,
          "user_wired_recovery_approval":"I can confirm wired recovery for balcony go ahead",
          "user_resume_approval":"Resume balcony again",
          "physical_load_unplugged_owner_confirmed":True,
          "approved_for_this_exact_transition_only":True,
          "full_chip_readback_verified":False,"physical_wired_recovery_tested":False,
          "hardware_acceptance":False,
          "source_confirmed_by_fresh_interview":str(mf),
          "source_interview_sha256":msha,
          "source_confirmed_by_fresh_ota_check":str(of),
          "source_ota_check_sha256":osha}
    rf=tmp_path/"risk.json";write(rf,risk)
    profile={
      "balcony_cli2_to_r18_transition":True,
      "device":"BedroomSocketBalcony","ieee":mod.IEEE,"manufacturer":"o1jzcxou",
      "model":"TS011F-BS","preflash_role":"EndDevice","postflash_role":"Router",
      "preflash_build":"1.1.2-bseedcli2","postflash_build":"1.1.3-bseedr18",
      "preflash_file_version":"0x1102300d","preflash_query_image_type":65026,
      "preflash_relay_physical_mode":mod.POLICY,"image_type":65026,
      "file_version":"0x1102301d","sha256":mod.WRAPPER_SHA,
      "native_sha256":mod.NATIVE_SHA,"manufacturer_code":4417,
      "force_test_transition":False,"non_pm":True,"require_pm":False,
      "expect_relay":"OFF","relay_get_key":"state_relay","parallel_ota_max":2,
      "concurrent_ota_ieees":["0xa4c13824a7005afb"],
      "source_rejoin_strategy":"scoped","join_via":"BedroomSocketCabinetL",
      "join_seconds":120,"allow_join_all_fallback":False,
      "network_id":"0x00124b002d12b1fd","network_lock_shared":True,
      "block_bytes":32,"response_delay_ms":1800,"request_timeout_ms":300000,
      "monitor_seconds":14400,
      "native_image":str(native_path),"image":str(wrapper_path),
      "url":"https://example.org/transport.ota",
      "target_recovery_risk_evidence":str(rf),
      "raw_source_metadata_evidence":str(mf),
      "raw_source_ota_check_evidence":str(of),
    }
    return profile,meta,mf,ota,of,risk,rf


def test_exact_legacy_transition_has_no_claimed_wired_readback(evidence):
    p,*_=evidence
    proof=mod.exact_transition(p)
    assert proof["source_role"]=="EndDevice" and proof["target_role"]=="Router"
    assert proof["source_version"]<proof["target_version"]
    assert proof["readback_verified"] is False
    with pytest.raises(ValueError, match="Physical load"):
        verify_transition_recovery(p,confirm_unloaded=False)
    assert verify_transition_recovery(p,confirm_unloaded=True)["wired_recovery_tested"] is False
    assert relay_policy_matches({"relay_physical_mode":None},p)
    assert not relay_policy_matches({"relay_physical_mode":"follow_state"},p)


@pytest.mark.parametrize("field,changed",[
  ("device","AnotherSocket"),("ieee","0xa4c138ac993cc246"),
  ("preflash_build","1.1.3-bseedv8"),("preflash_role","Router"),
  ("postflash_role","EndDevice"),("postflash_build","1.1.3-bseedr19"),
  ("preflash_file_version","0x11023001"),
  ("preflash_relay_physical_mode","follow_state"),
  ("file_version","0x1102301e"),("image_type",43555),
  ("sha256","0"*64),("native_sha256","0"*64),
  ("parallel_ota_max",1),("concurrent_ota_ieees",[]),
  ("source_rejoin_strategy","none"),("join_via","BedroomSocketCabinetRight"),
  ("force_test_transition",True),("allow_join_all_fallback",True),
  ("block_bytes",64),("response_delay_ms",1500),
  ("manufacturer","b28wrpvx"),("non_pm",False),("require_pm",True),
  ("expect_relay","ON"),
])
def test_foreign_device_or_profile_is_rejected(evidence,field,changed):
    p,*_=evidence;p[field]=changed
    with pytest.raises(ValueError):
        mod.exact_transition(p)


def test_mutated_interview_evidence_rejected(evidence):
    p,meta,mf,*_=evidence
    meta["after"]["software_build_id"]="1.1.2-bseedv8"
    write(mf,meta)
    with pytest.raises(ValueError,match="digest"):
        mod.exact_transition(p)


def test_source_ota_version_conflict_rejected(evidence):
    p,meta,mf,ota,of,risk,rf=evidence
    ota["updates"][-1][1]["installed_version"]=285356033
    risk["source_ota_check_sha256"]=write(of,ota)
    write(rf,risk)
    with pytest.raises(ValueError,match="source OTA"):
        mod.exact_transition(p)


def test_full_chip_backup_is_never_implied(evidence):
    p,meta,mf,ota,of,risk,rf=evidence
    risk["full_chip_readback_verified"]=True
    write(rf,risk)
    with pytest.raises(ValueError,match="authorization"):
        mod.exact_transition(p)
