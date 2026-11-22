"""Exact read-only software-successor proof for a superseded Balcony OTA lock.

A verified c17 installation supersedes the older *network ownership* concern,
not the older campaign's negative result or physical/hardware acceptance.
All source paths are private and explicit: no general fleet-wide bypass.
"""
from __future__ import annotations
import datetime as dt
import hashlib
from pathlib import Path
from bseed_historical_lock_supersession import _private_json, _date

DEVICE = "BedroomSocketBalcony"
IEEE = "0xa4c138ac993cc245"
OLD_TOKEN = "bseed-ota-7b842b26e5db486b82dbec31fe5dbea8"
OLD_SHA = "347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe"
NEW_SHA = "3ae273707a657b79a8bd2b7f6f77794d57547ba3a52247627b4a7c335f932b08"
NEW_BUILD = "1.1.3-bseedc17"
NEW_VERSION = 285356062
OLD_PHASE = "interrupted_source_identity_mismatch_operator_isolated_network_released"
CLEAN_PHASE = "postflash_software_verified_network_released"


def proven(config, *, old, old_path, live, now=None):
    """Fail closed; only this exact historical lock and later c17 OTA are eligible."""
    required = {"old_lock", "successor_lock", "software_evidence",
                "postflash_evidence", "authorize_new_same_role_ota"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Exact Balcony software-successor evidence paths required")
    if config["authorize_new_same_role_ota"] is not True:
        raise ValueError("No operator grant for a new independently gated OTA")
    old_path = Path(old_path).resolve()
    if Path(config["old_lock"]).resolve() != old_path:
        raise ValueError("The operator grant is not for this old work lock")
    if (old.get("device"), old.get("ieee"), old.get("token"),
            old.get("sha256"), old.get("phase"), old.get("hardware_acceptance"),
            old.get("same_ieee_new_ota_authorized")) != (
            DEVICE, IEEE, OLD_TOKEN, OLD_SHA, OLD_PHASE, False, False):
        raise ValueError("Old negative campaign record differs from pinned evidence")
    # Network ownership for that old token must already have been released.
    path = Path(old.get("network_lock") or "")
    if path.is_file():
        raise ValueError("Old campaign still holds a coordinator lease")
    _, successor = _private_json(config["successor_lock"])
    _, release = _private_json(config["software_evidence"])
    post_path, post = _private_json(config["postflash_evidence"])
    response = successor.get("response") or {}
    transfer = response.get("data") or {}
    if (successor.get("device"), successor.get("ieee"),
            successor.get("phase"), successor.get("sha256"),
            response.get("status"), response.get("transaction"),
            transfer.get("id"), (transfer.get("to") or {}).get("software_build_id"),
            (transfer.get("to") or {}).get("file_version")) != (
            DEVICE, IEEE, CLEAN_PHASE, NEW_SHA, "ok", successor.get("token"),
            IEEE, NEW_BUILD, NEW_VERSION):
        raise ValueError("Later c17 OTA has no verified matching success")
    if (release.get("device"), release.get("ieee"), release.get("token"),
            release.get("sha256"), release.get("installed_build"),
            release.get("installed_role"), release.get("result"),
            release.get("softwareVerification"), release.get("networkOwnershipReleased"),
            release.get("hardwareAcceptance")) != (
            DEVICE, IEEE, successor["token"], NEW_SHA, NEW_BUILD,
            "EndDevice", CLEAN_PHASE, True, True, False):
        raise ValueError("Later c17 software-release proof disagrees")
    post_digest = hashlib.sha256(post_path.read_bytes()).hexdigest()
    records = (release.get("evidence") or {}).get(post_path.name) or {}
    if records.get("path") != str(post_path) or records.get("sha256") != post_digest:
        raise ValueError("Original c17 postflash digest not matched")
    if (post.get("result"), post.get("issues"), post.get("errors"),
            post.get("target_name"), post.get("target_ieee"),
            post.get("expected_build"), (post.get("live_node_descriptor") or {}).get("role"),
            (post.get("state") or {}).get("state_relay")) != (
            "postflash_candidate", [], [], DEVICE, IEEE, NEW_BUILD, "EndDevice", "OFF"):
        raise ValueError("Later c17 postflash was not independently clean")
    now = now or dt.datetime.now(dt.timezone.utc)
    last = live.get("lastSeen")
    if not isinstance(last, (int, float)):
        raise ValueError("Current live source has no observation timestamp")
    observed = dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc)
    if not (_date(old["started"]) < _date(successor["started"])
            < _date(successor["completed"]) <= _date(release["at"]) <= observed):
        raise ValueError("Successor chronology is inconsistent")
    if not dt.timedelta(seconds=-30) <= now-observed <= dt.timedelta(minutes=15):
        raise ValueError("Installed c17 source observation is stale")
    if (live.get("ieeeAddr"), live.get("swBuildId"), live.get("type"),
            live.get("manufName"), live.get("modelId"), live.get("interviewCompleted")) != (
            IEEE, NEW_BUILD, "EndDevice", "o1jzcxou", "TS011F-BS", True):
        raise ValueError("Current Zigbee source differs from accepted c17 software successor")
    return True
