"""Portable tests: the exact weak-link risk exception never fakes Zigbee success."""
import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
from bseed_balcony_link_operator_override import verify_operator_override


@pytest.fixture
def example(tmp_path, monkeypatch):
    from bseed_nonpm_relay_policy import is_exact_balcony_router_candidate
    monkeypatch.setattr(
        "bseed_nonpm_relay_policy.is_exact_balcony_router_candidate",
        lambda profile: profile.get("device") == "BedroomSocketBalcony",
    )
    work = tmp_path / "operator-risk-campaign"
    work.mkdir()
    gate = {
        "schema": 1, "passed": False, "device": "BedroomSocketBalcony",
        "ieee": "0xa4c138ac993cc245",
        "image_sha256": "347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe",
        "build": "1.1.3-bseedv8", "last_error": "Read timeout",
    }
    failed = tmp_path / "original_link_failure.json"
    failed.write_text(json.dumps(gate))
    user_auth = {
        "schema": 1,
        "approved_at_unix": time.time(),
        "approval": "I can confirm wired recovery for balcony go ahead",
        "device": "BedroomSocketBalcony",
        "ieee": "0xa4c138ac993cc245",
        "target_sha256": gate["image_sha256"],
        "physically_unplugged": True,
        "wired_recovery_user_attested": True,
        "readback_files_verified": False,
        "passed_link_probe": False,
        "original_failed_link_file": str(failed),
        "original_failed_link_sha256": hashlib.sha256(failed.read_bytes()).hexdigest(),
    }
    auth_file = tmp_path / "operator_approval.json"
    auth_file.write_text(json.dumps(user_auth))
    profile = {
        "device": "BedroomSocketBalcony", "ieee": "0xa4c138ac993cc245",
        "manufacturer": "o1jzcxou", "model": "TS011F-BS",
        "preflash_role": "Router", "postflash_role": "Router",
        "preflash_build": "1.1.3-bseedv8", "postflash_build": "1.1.3-bseedr18",
        "sha256": gate["image_sha256"],
        "native_sha256": "4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9",
        "image_type": 65026, "file_version": "0x1102301d",
        "preflash_query_image_type": 65026,
        "non_pm": True, "require_pm": False,
        "parallel_ota_max": 2, "concurrent_ota_ieees": ["0xa4c13824a7005afb"],
        "source_rejoin_strategy": "none", "allow_join_all_fallback": False,
        "block_bytes": 32, "response_delay_ms": 1800,
        "force_test_transition": False,
        "expect_relay": "OFF", "relay_get_key": "state_relay",
        "preflash_relay_physical_mode": "legacy_unreported_v8_router",
        "workdir": str(work), "weak_link_operator_authorization": str(auth_file),
        "url": "https://example.org/example.ota",
    }
    return profile, gate, user_auth, auth_file, failed


def test_exact_permission_keeps_failure_explicit(example):
    p, gate, *_ = example
    result = verify_operator_override(p, gate)
    assert result["operator_risk_accepted"] is True
    assert result["standard_link_gate_passed"] is False
    assert result["wired_recovery_tested"] is False


@pytest.mark.parametrize("name,bad", [
    ("device","Wrong"),("ieee","0xa4c138ac993cc246"),
    ("sha256","0"*64),("native_sha256","0"*64),("image_type",43555),
    ("preflash_build","1.1.3-bseedc16"),("postflash_build","1.1.3-bseedr19"),
    ("parallel_ota_max",1),("concurrent_ota_ieees",[]),
    ("source_rejoin_strategy","auto"),("allow_join_all_fallback",True),
    ("block_bytes",64),("response_delay_ms",1200),("expect_relay","ON"),
    ("force_test_transition",True),("workdir","/tmp/shared-campaign"),
])
def test_other_device_or_firmware_not_authorized(example, name, bad):
    p,gate,*_=example
    p[name]=bad
    with pytest.raises(AssertionError):
        verify_operator_override(p,gate)


def test_forged_success_is_rejected(example):
    p,gate,*_=example
    gate["passed"]=True
    with pytest.raises(AssertionError):
        verify_operator_override(p,gate)


def test_mutated_original_failure_is_rejected(example):
    p,gate,auth,authfile,failed=example
    failed.write_text("{}")
    with pytest.raises(AssertionError):
        verify_operator_override(p,gate)


def test_operator_approval_cannot_be_reused_forever(example):
    p,gate,*_=example
    with pytest.raises(AssertionError,match="too old"):
        verify_operator_override(p,gate,clock=time.time()+14410)


def test_flash_requires_fresh_exact_check(example):
    p,gate,*_=example
    after=time.time()
    with pytest.raises(FileNotFoundError):
        verify_operator_override(p,gate,after=after)
    check={
        "timestamp":after,"device":p["device"],"ieee":p["ieee"],"sha256":p["sha256"],
        "response":{
            "status":"ok",
            "data":{"id":p["ieee"],"update_available":True,"downgrade":False,
                    "source":p["url"]}
        },
    }
    (Path(p["workdir"])/"LAST_CHECK.json").write_text(json.dumps(check))
    assert verify_operator_override(p,gate,after=after)["operator_risk_accepted"] is True
    check["response"]["data"]["source"]="https://invalid.example.org/foreign.ota"
    (Path(p["workdir"])/"LAST_CHECK.json").write_text(json.dumps(check))
    with pytest.raises(AssertionError):
        verify_operator_override(p,gate,after=after)
