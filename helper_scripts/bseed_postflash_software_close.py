"""Close ONE successful OTA network lease after exact software evidence only.

This deliberately does NOT mark the device hardware-accepted. Its sole purpose
is to separate network-wide OTA exclusivity from slower physical/soak gates.

The original campaign work lock and immutable evidence remain available. Only
the exact shared lease matching the authenticated work lock is released.
No MQTT, coordinator, Zigbee or power-switch commands are sent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from bseed_network_campaign_lock import (
    SOFTWARE_VERIFIED_PHASE, read_lock, release_software_verified, update,
)
from bseed_ota_campaign import ROOT, load_profile, network_lock_path

MAX_EVIDENCE_AGE_SECONDS = 3600


def private_json(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.is_relative_to(ROOT):
        raise ValueError("Evidence must be existing private JSON outside repository")
    return path, json.loads(path.read_text(encoding="utf8"))


def timestamp(text):
    value = dt.datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("Timezone-aware evidence timestamp required")
    return value.astimezone(dt.timezone.utc)


def validate(profile, work, network, post, audit, prior_audit, baseline,
             *, now=None):
    """Pure fail-closed validator: no lock mutation or hardware access."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if profile["preflash_role"] != profile["postflash_role"]:
        raise ValueError("Software closure requires same-role OTA")
    if (work.get("phase"), network.get("phase")) != (
            "postflash_candidate", "postflash_candidate"):
        raise ValueError("Software closure requires postflash_candidate on BOTH locks")
    token = work.get("token")
    if not token or token != network.get("token"):
        raise ValueError("Work/network ownership token mismatch")
    if any((
        work.get("device") != profile["device"],
        network.get("device") != profile["device"],
        work.get("ieee") != profile["ieee"],
        network.get("ieee") != profile["ieee"],
        work.get("sha256") != profile["sha256"],
        network.get("image_sha256") != profile["sha256"],
        network.get("network_id") != profile["network_id"],
    )):
        raise ValueError("Work/network campaign identity mismatch")

    response = work.get("response") or {}
    transfer = response.get("data") or {}
    expected_version = int(str(profile["file_version"]), 0)
    if not (
        response.get("status") == "ok"
        and response.get("transaction") == token
        and transfer.get("id") == profile["ieee"]
        and (transfer.get("to") or {}).get("software_build_id") == profile["postflash_build"]
        and (transfer.get("to") or {}).get("file_version") == expected_version
    ):
        raise ValueError("Exact transaction-matched successful OTA transport required")

    started = timestamp(work["started"])
    completed = timestamp(work["completed"])
    post_at = timestamp(post["observed_at"])
    if not started < completed <= post_at <= now + dt.timedelta(seconds=15):
        raise ValueError("Impossible OTA/postflash event chronology")
    if (now - post_at).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
        raise ValueError("Postflash evidence too old; re-run fresh postflash check")
    if post.get("result") != "postflash_candidate" or post.get("issues") or post.get("errors"):
        raise ValueError("Independent postflash candidate evidence is not clean")
    if post.get("bridge") != "online" or post.get("fresh_mqtt_state_observed") is not True:
        raise ValueError("Live Zigbee coordinator/device evidence missing")
    if post.get("zdo_error") is not None or post.get("cached_inventory_role_disagrees_with_live_zdo"):
        raise ValueError("Unverified or inconsistent live ZDO role")
    if (post.get("live_node_descriptor") or {}).get("role") != profile["postflash_role"]:
        raise ValueError("Fresh node-descriptor role mismatch")
    for name, expected in (
        ("target_name", profile["device"]),
        ("target_ieee", profile["ieee"]),
        ("expected_role", profile["postflash_role"]),
        ("expected_build", profile["postflash_build"]),
    ):
        if post.get(name) != expected:
            raise ValueError("Postflash target identity mismatch: " + name)
    inventory = post.get("inventory") or {}
    if (inventory.get("ieee_address"), inventory.get("software_build_id"),
            inventory.get("type"), inventory.get("interview_completed")) != (
            profile["ieee"], profile["postflash_build"], profile["postflash_role"], True):
        raise ValueError("Actual postflash inventory build/role missing")
    reported_device = (post.get("state") or {}).get("device") or {}
    if (reported_device.get("ieeeAddr"), reported_device.get("softwareBuildID"),
            reported_device.get("type")) != (
            profile["ieee"], profile["postflash_build"], profile["postflash_role"]):
        raise ValueError("Fresh device state does not confirm installed build/role")
    if (post.get("same_role_retention") or {}).get("issues"):
        raise ValueError("Same-role retention checks failed")
    if (post.get("pm_readiness") or {}).get("issues"):
        raise ValueError("PM readiness not proven")
    if (post.get("state") or {}).get("state_relay") != (
            work.get("preflash_state") or {}).get("state_relay"):
        raise ValueError("Preflash relay state not preserved")

    if profile.get("require_pm") is True:
        if not all(isinstance(v, dict) for v in (audit, prior_audit, baseline)):
            raise ValueError("Independent preflash/postflash PM audits required")
        prior_at = timestamp(prior_audit["at"])
        audit_at = timestamp(audit["at"])
        if not prior_at < started < completed < audit_at <= now + dt.timedelta(seconds=15):
            raise ValueError("PM audit chronology not independently verified")
        if (now - audit_at).total_seconds() > MAX_EVIDENCE_AGE_SECONDS * 4:
            raise ValueError("Postflash PM audit stale")
        for rec, build in ((prior_audit, profile["preflash_build"]),
                           (audit, profile["postflash_build"])):
            if (rec.get("device"), rec.get("ieee"), rec.get("expected_role"),
                    rec.get("expected_build"), rec.get("result")) != (
                    profile["device"], profile["ieee"], profile["postflash_role"],
                    build, "read_only_audit_candidate"):
                raise ValueError("PM audit identity, build or result mismatch")
            if rec.get("issues") or rec.get("writes") != 0 or rec.get("missing_reporting"):
                raise ValueError("PM audit has errors, writes or reporting gaps")
        if (baseline.get("ieee"), baseline.get("role")) != (
                profile["ieee"], profile["preflash_role"]):
            raise ValueError("Preflash baseline identity mismatch")
        original_settings = baseline.get("settings_snapshot")
        if not isinstance(original_settings, dict) or not original_settings:
            raise ValueError("Settings baseline missing")
        if (prior_audit.get("settings_snapshot") != original_settings
                or audit.get("settings_snapshot") != original_settings
                or audit.get("settings_drift")):
            raise ValueError("Preflash settings baseline not retained")
        last = (audit.get("mqtt") or {}).get("latest_metrics") or {}
        previous_energy = (work.get("preflash_state") or {}).get("energy")
        energy = last.get("energy")
        if not all(type(x) in (int, float) and math.isfinite(x)
                   for x in (previous_energy, energy)):
            raise ValueError("PM cumulative energy comparison missing")
        if energy < previous_energy:
            raise ValueError("Cumulative energy regressed")
        if int((audit.get("mqtt") or {}).get("messages") or 0) < 2 or (
                (audit.get("mqtt") or {}).get("span_seconds") or 0) < 8:
            raise ValueError("Separated live PM telemetry required")
        if not (180 <= last.get("voltage", 0) <= 260):
            raise ValueError("PM voltage out of range")

    # This establishes software readiness only, never physical hardware acceptance.
    return {
        "device": profile["device"], "ieee": profile["ieee"],
        "installed_build": profile["postflash_build"],
        "installed_role": profile["postflash_role"],
        "token": token, "sha256": profile["sha256"],
        "otaTransportSuccess": True, "softwareVerification": True,
        "hardwareAcceptance": False, "physicalAcceptance": False,
    }


def close(args):
    profile = load_profile(args.profile)
    if profile["ieee"] != args.confirm_ieee:
        raise ValueError("Exact IEEE confirmation required")
    image = Path(profile["image"])
    if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != profile["sha256"]:
        raise ValueError("Original pinned OTA bytes missing or changed")

    workdir = Path(profile["workdir"]).resolve()
    work_path, work = private_json(workdir / "ACTIVE_LOCK.json")
    network_path = network_lock_path(profile, required=True)
    network = read_lock(network_path)
    if network is None:
        raise ValueError("Exact active network ownership required")

    post_path, post = private_json(args.postflash_evidence)
    prior_path, prior = private_json(args.preflash_audit_evidence)
    audit_path, audit = private_json(args.pm_audit_evidence)
    baseline_path, baseline = private_json(args.preflash_baseline)
    facts = validate(profile, work, network, post, audit, prior, baseline)

    output = Path(args.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use fresh immutable private evidence outside repository")

    files = (post_path, prior_path, audit_path, baseline_path)
    record = {
        **facts, "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "result": SOFTWARE_VERIFIED_PHASE,
        "networkOwnershipReleased": False,
        "evidence": {
            p.name: {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in files
        },
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **record}, indent=2))
        return record

    # Save proof before any lock mutation. A failure leaves a visible closed-or-
    # blocked state, never silently an accepted hardware release.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")

    work["phase"] = SOFTWARE_VERIFIED_PHASE
    work["software_verified_at"] = record["at"]
    work["hardware_acceptance"] = False
    work["physical_acceptance"] = False
    work["software_evidence"] = str(output)
    work_path.write_text(json.dumps(work, indent=2) + "\n", encoding="utf8")
    update(network_path, facts["token"], SOFTWARE_VERIFIED_PHASE,
           software_verified_at=record["at"], hardware_acceptance=False,
           physical_acceptance=False, software_evidence=str(output))
    release_software_verified(network_path, facts["token"])

    record["networkOwnershipReleased"] = True
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf8")
    print(json.dumps(record, indent=2))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--confirm-ieee", required=True)
    parser.add_argument("--postflash-evidence", required=True)
    parser.add_argument("--pm-audit-evidence", required=True)
    parser.add_argument("--preflash-audit-evidence", required=True)
    parser.add_argument("--preflash-baseline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    close(args)


if __name__ == "__main__":
    main()
