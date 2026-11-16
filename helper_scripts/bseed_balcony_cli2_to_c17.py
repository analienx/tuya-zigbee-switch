"""Exact, same-role Balcony legacy cli2 -> sealed non-PM c17 OTA admission.

Never permits a Router payload, foreign IEEE, role change, FORCE image,
or a source version extrapolated from stale Zigbee2MQTT inventory.
"""
from pathlib import Path
import datetime as dt
import hashlib
import json
import struct

IEEE = "0xa4c138ac993cc245"
IMAGE_SHA = "3ae273707a657b79a8bd2b7f6f77794d57547ba3a52247627b4a7c335f932b08"
SOURCE = "1.1.2-bseedcli2"
TARGET = "1.1.3-bseedc17"
EXPECTED = {
    "balcony_legacy_cli2_to_c17": True,
    "device": "BedroomSocketBalcony","ieee":IEEE,
    "manufacturer":"o1jzcxou","model":"TS011F-BS",
    "preflash_role":"EndDevice","postflash_role":"EndDevice",
    "preflash_build":SOURCE,"postflash_build":TARGET,
    "preflash_file_version":"0x1102300d","preflash_query_image_type":65026,
    "image_type":65026,"manufacturer_code":4417,"file_version":"0x1102301e",
    "sha256":IMAGE_SHA,"non_pm":True,"require_pm":False,
    "force_test_transition":False,
    "preflash_relay_physical_mode":"legacy_unreported_cli2_client",
    "relay_get_key":"state_relay","expect_relay":"OFF",
    "parallel_ota_max":1,"concurrent_ota_ieees":[],
    "network_lock_shared":True,"source_rejoin_strategy":"none",
    "block_bytes":32,"response_delay_ms":1800,
    "request_timeout_ms":300000,"allow_join_all_fallback":False,
    "network_id":"0x00124b002d12b1fd",
}
def verify_exact(profile, *, check_age=True):
    for k,v in EXPECTED.items():
        if profile.get(k) != v:
            raise ValueError("Exact legacy Balcony cli2->c17 rejected: "+k)
    img=Path(profile["image"])
    if not img.is_file() or hashlib.sha256(img.read_bytes()).hexdigest()!=IMAGE_SHA:
        raise ValueError("Sealed non-PM c17 image SHA does not match")
    b=img.read_bytes()
    if struct.unpack_from("<HHI",b,10)!=(4417,65026,0x1102301e):
        raise ValueError("c17 Client OTA header mismatch")
    proof_file=Path(profile.get("raw_source_metadata_evidence") or "")
    if not proof_file.is_file() or not proof_file.is_absolute():
        raise ValueError("Private live source-identity evidence absent")
    raw=proof_file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=profile.get("raw_source_metadata_sha256"):
        raise ValueError("Live source evidence digest mismatch")
    proof=json.loads(raw)
    if not (
        proof.get("device")=="BedroomSocketBalcony" and
        proof.get("ieee")==IEEE and
        proof.get("result") in ("metadata_refreshed","metadata_already_correct") and
        proof.get("error") is None and
        proof.get("interview_ok") is True and
        proof.get("fresh_inventory_observed") is True and
        proof.get("inventory_role_after")=="EndDevice" and
        (proof.get("live_zdo_after") or {}).get("role")=="EndDevice" and
        (proof.get("after") or {}).get("software_build_id")==SOURCE and
        (proof.get("after") or {}).get("ieee_address")==IEEE and
        (proof.get("after") or {}).get("type")=="EndDevice" and
        (proof.get("after") or {}).get("interview_completed") is True
    ):
        raise ValueError("Fresh actual cli2 EndDevice interview/source proof failed")
    if check_age:
        at=dt.datetime.fromisoformat(proof["at"])
        now=dt.datetime.now(dt.timezone.utc)
        if at.tzinfo is None or not 0 <= (now-at).total_seconds() <= 3600:
            raise ValueError("Source proof older than one hour; re-interview")
    return {
        "device":IEEE,"board":"o1jzcxou",
        "source_role":"EndDevice","target_role":"EndDevice",
        "source_version":0x1102300d,"target_version":0x1102301e,
        "image_sha256":IMAGE_SHA,
        "same_role":True,"force_test_transition":False,
        "hardware_acceptance":False,"postflash_verification_pending":True,
    }
