"""Strictly scoped legacy Client->Router OTA candidate, never a general bypass.

The old cli2 build is absent from the current OTA identity registry. It was
confirmed on 2026-11-15 by fresh targeted Zigbee2MQTT interview, a live ZDO
EndDevice descriptor, and an OTA eligibility query reporting 0x1102300d.
The sealed r18 native Router payload is retained without modifications;
its outer OTA transport type is changed to answer cli2's type 65026 query.

No full SWire readback or recovery test is claimed. Separate explicit user
authorization for this particular high-risk role migration is retained.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
import struct

IEEE = "0xa4c138ac993cc245"
NATIVE_SHA = "4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9"
WRAPPER_SHA = "347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe"
POLICY = "legacy_unreported_cli2_client"
SRC_VERSION = 0x1102300d
DST_VERSION = 0x1102301d


def _load_pinned(path, expected_hash):
    f = Path(path)
    if not f.is_absolute() or not f.is_file():
        raise ValueError("Private evidence file missing")
    raw = f.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Private evidence digest changed")
    return json.loads(raw)


def exact_transition(profile, *, check_age=True):
    """Validate one specific target, signed payload and operator permission.

    Does NOT touch MQTT, Zigbee, serial, network locks or firmware state.
    Return proof with explicit negative hardware/recovery findings.
    """
    if profile.get("balcony_cli2_to_r18_transition") is not True:
        raise ValueError("Exact Balcony legacy transition marker absent")
    required = {
        "device": "BedroomSocketBalcony",
        "ieee": IEEE,
        "manufacturer": "o1jzcxou",
        "model": "TS011F-BS",
        "preflash_role": "EndDevice",
        "postflash_role": "Router",
        "preflash_build": "1.1.2-bseedcli2",
        "postflash_build": "1.1.3-bseedr18",
        "preflash_file_version": "0x1102300d",
        "preflash_query_image_type": 65026,
        "preflash_relay_physical_mode": POLICY,
        "image_type": 65026,
        "file_version": "0x1102301d",
        "sha256": WRAPPER_SHA,
        "native_sha256": NATIVE_SHA,
        "manufacturer_code": 4417,
        "force_test_transition": False,
        "non_pm": True,
        "require_pm": False,
        "expect_relay": "OFF",
        "relay_get_key": "state_relay",
        "parallel_ota_max": 1,
        "concurrent_ota_ieees": [],
        "source_rejoin_strategy": "scoped",
        "join_via": "BedroomSocketCabinetL",
        "join_seconds": 120,
        "allow_join_all_fallback": False,
        "network_id": "0x00124b002d12b1fd",
        "network_lock_shared": True,
        "block_bytes": 32,
        "response_delay_ms": 1800,
        "request_timeout_ms": 300000,
        "monitor_seconds": 14400,
    }
    for k,v in required.items():
        if profile.get(k) != v:
            raise ValueError("Legacy Client->Router identity mismatch: "+k)
    native_path, wrapper_path = Path(profile["native_image"]), Path(profile["image"])
    if not native_path.is_absolute() or not wrapper_path.is_absolute():
        raise ValueError("Firmware paths must be absolute")
    native, wrapper = native_path.read_bytes(), wrapper_path.read_bytes()
    if hashlib.sha256(native).hexdigest()!=NATIVE_SHA or hashlib.sha256(wrapper).hexdigest()!=WRAPPER_SHA:
        raise ValueError("Native or transport image digest mismatch")
    if not len(wrapper)==len(native) or wrapper[56:]!=native[56:]:
        raise ValueError("Transport wrapper changed sealed payload")
    if struct.unpack_from("<HHI",native,10)!=(4417,43555,DST_VERSION) or (
       struct.unpack_from("<HHI",wrapper,10)!=(4417,65026,DST_VERSION)):
        raise ValueError("Wrong native Router/Client transport identity")
    expected=bytearray(native)
    struct.pack_into("<H",expected,12,65026)
    if wrapper!=bytes(expected):
        raise ValueError("Transport may modify only its image-type field")

    risk_path = profile.get("target_recovery_risk_evidence")
    if not risk_path:
        raise ValueError("Explicit migration-risk authorization missing")
    risk = json.loads(Path(risk_path).read_text(encoding="utf8"))
    if not all([
        risk.get("schema")==1, risk.get("device")=="BedroomSocketBalcony",
        risk.get("ieee")==IEEE, risk.get("source_build")=="1.1.2-bseedcli2",
        risk.get("source_role")=="EndDevice", risk.get("source_file_version")==SRC_VERSION,
        risk.get("target_build")=="1.1.3-bseedr18", risk.get("target_role")=="Router",
        risk.get("target_file_version")==DST_VERSION,
        risk.get("transport_sha256")==WRAPPER_SHA, risk.get("native_sha256")==NATIVE_SHA,
        risk.get("user_wired_recovery_approval")=="I can confirm wired recovery for balcony go ahead",
        risk.get("user_resume_approval")=="Resume balcony again",
        risk.get("physical_load_unplugged_owner_confirmed") is True,
        risk.get("approved_for_this_exact_transition_only") is True,
        risk.get("full_chip_readback_verified") is False,
        risk.get("physical_wired_recovery_tested") is False,
        risk.get("hardware_acceptance") is False,
    ]):
        raise ValueError("Exact operator risk authorization incomplete")

    meta_path = profile.get("raw_source_metadata_evidence")
    ota_path = profile.get("raw_source_ota_check_evidence")
    if (str(meta_path)!=risk.get("source_confirmed_by_fresh_interview") or
        str(ota_path)!=risk.get("source_confirmed_by_fresh_ota_check")):
        raise ValueError("Profile source evidence not pinned to user approval")
    meta = _load_pinned(meta_path,risk["source_interview_sha256"])
    ota = _load_pinned(ota_path,risk["source_ota_check_sha256"])
    if not (meta.get("device")=="BedroomSocketBalcony" and meta.get("ieee")==IEEE and
        meta.get("result")=="metadata_refreshed" and meta.get("interview_ok") is True and
        meta.get("fresh_inventory_observed") is True and
        (meta.get("live_zdo_after") or {}).get("role")=="EndDevice" and
        meta.get("inventory_role_after")=="EndDevice" and
        (meta.get("after") or {}).get("software_build_id")=="1.1.2-bseedcli2" and
        (meta.get("after") or {}).get("type")=="EndDevice"):
        raise ValueError("Fresh source Client interview missing or conflicting")
    checks = ota.get("updates") or []
    latest = checks[-1][1] if checks and isinstance(checks[-1],list) and len(checks[-1])==2 else {}
    response = ota.get("response") or {}
    data = response.get("data") or {}
    inv=ota.get("inventory") or []
    if not (ota.get("status")=="availability_only" and ota.get("update_command_sent") is False and
        response.get("status")=="ok" and data.get("id")==IEEE and
        data.get("update_available") is True and data.get("source")==profile.get("url") and
        len(inv)==1 and inv[0].get("ieee")==IEEE and inv[0].get("role")=="EndDevice" and
        inv[0].get("build")=="1.1.2-bseedcli2" and
        latest.get("installed_version")==SRC_VERSION and
        latest.get("latest_version")==DST_VERSION):
        raise ValueError("Fresh source OTA version/role proof missing")
    if check_age:
        now=dt.datetime.now(dt.timezone.utc)
        for recorded in (meta.get("at"),ota.get("at"),risk.get("at")):
            t=dt.datetime.fromisoformat(recorded)
            if t.tzinfo is None or not 0 <= (now-t.astimezone(dt.timezone.utc)).total_seconds() <= 7200:
                raise ValueError("Legacy source/authorization proof expired; refresh evidence")
    return {
        "board":"o1jzcxou","device":IEEE,
        "source_role":"EndDevice","target_role":"Router",
        "source_version":SRC_VERSION,"target_version":DST_VERSION,
        "method":"exact, user-authorized legacy cli2 EndDevice -> sealed r18 Router",
        "transport_sha256":WRAPPER_SHA,"native_sha256":NATIVE_SHA,
        "operator_risk_accepted":True,"readback_verified":False,
        "wired_recovery_tested":False,"hardware_acceptance":False,
        "warning":"Role transition needs postflash rejoin; no physical SWire restore or two verified full-chip backups."
    }
