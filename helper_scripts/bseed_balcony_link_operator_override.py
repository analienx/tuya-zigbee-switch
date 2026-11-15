"""Exact Balcony-only operator-approved weak-link OTA risk exception.

NOT proof of Zigbee reachability or SWire success. The normal MQTT relay
GET, image/version/role, OTA source check, and network lease MUST still pass.
Keep the failed diagnostic record intact. No blanket skip for any other OTA.
"""
from pathlib import Path
import hashlib
import json
import time

BALCONY_IEEE = "0xa4c138ac993cc245"
ROUTER_SHA = "347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe"
NATIVE_SHA = "4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9"
PEER_IEEE = "0xa4c13824a7005afb"
APPROVAL = "I can confirm wired recovery for balcony go ahead"


def verify_operator_override(profile, failed_gate, *, after=None, clock=None):
    """Require an exact device, immutable failure evidence, and explicit risk log."""
    clock = time.time() if clock is None else clock
    identity = {
        "device": "BedroomSocketBalcony",
        "ieee": BALCONY_IEEE,
        "manufacturer": "o1jzcxou",
        "model": "TS011F-BS",
        "preflash_role": "Router",
        "postflash_role": "Router",
        "preflash_build": "1.1.3-bseedv8",
        "postflash_build": "1.1.3-bseedr18",
        "sha256": ROUTER_SHA,
        "native_sha256": NATIVE_SHA,
        "image_type": 65026,
        "file_version": "0x1102301d",
        "preflash_query_image_type": 65026,
        "non_pm": True,
        "require_pm": False,
        "parallel_ota_max": 2,
        "concurrent_ota_ieees": [PEER_IEEE],
        "source_rejoin_strategy": "none",
        "allow_join_all_fallback": False,
        "block_bytes": 32,
        "response_delay_ms": 1800,
        "force_test_transition": False,
        "expect_relay": "OFF",
        "relay_get_key": "state_relay",
        "preflash_relay_physical_mode": "legacy_unreported_v8_router",
    }
    if any(profile.get(k) != value for k, value in identity.items()):
        raise AssertionError("Weak-link exception denied: unrelated target or firmware policy")
    if Path(profile["workdir"]).name != "operator-risk-campaign":
        raise AssertionError("Weak-link exception requires a fresh dedicated workdir")
    from bseed_nonpm_relay_policy import is_exact_balcony_router_candidate
    if not is_exact_balcony_router_candidate(profile):
        raise AssertionError("Native Router payload/wrapper integrity not verified")
    if not isinstance(failed_gate, dict) or failed_gate.get("passed") is not False:
        raise AssertionError("Actual failed link-gate evidence required; never forge a pass")
    for key, expected in (("device", identity["device"]), ("ieee", BALCONY_IEEE),
                           ("image_sha256", ROUTER_SHA), ("build", identity["preflash_build"])):
        if failed_gate.get(key) != expected:
            raise AssertionError("Failed link evidence mismatch: " + key)
    if not failed_gate.get("last_error"):
        raise AssertionError("Failed link must retain diagnostic reason")
    evidence_path = profile.get("weak_link_operator_authorization")
    if not evidence_path or not Path(evidence_path).is_absolute():
        raise AssertionError("Missing absolute operator authorization path")
    evidence = json.loads(Path(evidence_path).read_text(encoding="utf8"))
    if (evidence.get("schema") != 1 or evidence.get("approval") != APPROVAL or
            evidence.get("device") != "BedroomSocketBalcony" or
            evidence.get("ieee") != BALCONY_IEEE or
            evidence.get("target_sha256") != ROUTER_SHA or
            evidence.get("physically_unplugged") is not True or
            evidence.get("wired_recovery_user_attested") is not True or
            evidence.get("readback_files_verified") is not False or
            evidence.get("passed_link_probe") is not False):
        raise AssertionError("Exact Balcony operator approval unavailable or invalid")
    original = Path(evidence.get("original_failed_link_file", ""))
    if not original.is_absolute() or not original.is_file():
        raise AssertionError("Original failed link gate file missing")
    if hashlib.sha256(original.read_bytes()).hexdigest() != evidence.get("original_failed_link_sha256"):
        raise AssertionError("Failed link evidence changed after operator approval")
    if json.loads(original.read_text(encoding="utf8")) != failed_gate:
        raise AssertionError("Runner failed link gate differs from immutable source")
    approved = evidence.get("approved_at_unix")
    if not isinstance(approved, (float,int)) or not 0 <= clock - approved <= 14400:
        raise AssertionError("Operator authorization too old or in future")
    if after is not None:
        if not isinstance(after, (float,int)) or not 0 <= clock - after <= 300:
            raise AssertionError("A successful exact OTA availability check within 5 minutes is required")
        lastcheck = Path(profile["workdir"]) / "LAST_CHECK.json"
        check = json.loads(lastcheck.read_text(encoding="utf8"))
        response = check.get("response", {})
        data = response.get("data", {})
        if (check.get("timestamp") != after or check.get("device") != identity["device"] or
                check.get("ieee") != BALCONY_IEEE or
                check.get("sha256") != ROUTER_SHA or
                response.get("status") != "ok" or
                data.get("id") != BALCONY_IEEE or
                data.get("source") != profile["url"] or
                data.get("update_available") is not True or
                data.get("downgrade") is not False):
            raise AssertionError("No fresh matching Balcony OTA availability result")
    return {"operator_risk_accepted": True,
            "standard_link_gate_passed": False,
            "wired_recovery_tested": False,
            "live_relay_and_OTA_response_still_required": True,
            "device": BALCONY_IEEE,
            "failed_link_sha256": evidence["original_failed_link_sha256"]}
