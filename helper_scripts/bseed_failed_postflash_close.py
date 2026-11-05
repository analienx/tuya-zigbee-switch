"""Terminalize a completed OTA with proven failed PM retention.

Never changes hardware, metering data or the prior transport result.
Releases only exact matching network ownership after recording failure.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from bseed_canary import live_identity, load_site
from bseed_network_campaign_lock import (
    FAILED_POSTFLASH_PHASE, read_lock, update, release_failed_postflash,
)
from bseed_ota_campaign import ROOT, load_profile, network_lock_path

PREVIOUS = "ota_transfer_ok_postflash_unverified"
FAILURE = "Cumulative PM energy dropped across same-role OTA"


def when(value):
    out = dt.datetime.fromisoformat(value)
    if out.tzinfo is None:
        raise ValueError("Evidence timestamp must specify timezone")
    return out.astimezone(dt.timezone.utc)


def validate(profile, work, network, post, live, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if (profile.get("require_pm"), profile.get("preflash_role"),
            profile.get("postflash_role")) != (True, "Router", "Router"):
        raise ValueError("Only same-role PM Router failed retention can close")
    token = work.get("token")
    if not token or (work.get("phase"), network.get("phase")) != (PREVIOUS, PREVIOUS):
        raise ValueError("Both campaign records must be the unverified completion")
    if any([
        work.get("device") != profile["device"],
        network.get("device") != profile["device"],
        work.get("ieee") != profile["ieee"],
        network.get("ieee") != profile["ieee"],
        network.get("token") != token,
        work.get("sha256") != profile["sha256"],
        network.get("image_sha256") != profile["sha256"],
        network.get("network_id") != profile["network_id"],
    ]):
        raise ValueError("Owner, OTA digest or shared network mismatch")
    resp = work.get("response") or {}
    upgrade = (resp.get("data") or {}).get("to") or {}
    if (resp.get("status"), resp.get("transaction"),
            (resp.get("data") or {}).get("id"),
            upgrade.get("software_build_id"),
            upgrade.get("file_version")) != (
            "ok", token, profile["ieee"], profile["postflash_build"],
            int(str(profile["file_version"]), 0)):
        raise ValueError("Exact successful OTA transfer not demonstrated")
    if (post.get("result") != "unconfirmed" or post.get("issues") != [FAILURE]
            or post.get("errors")
            or (post.get("same_role_retention") or {}).get("issues") != [FAILURE]
            or (post.get("pm_readiness") or {}).get("issues")):
        raise ValueError("Retained negative PM evidence is missing or ambiguous")
    inv = post.get("inventory") or {}
    desc = post.get("live_node_descriptor") or {}
    if (post.get("bridge") != "online" or
            post.get("fresh_mqtt_state_observed") is not True or
            post.get("zdo_error") is not None or desc.get("role") != "Router" or
            post.get("target_ieee") != profile["ieee"] or
            post.get("expected_build") != profile["postflash_build"] or
            (inv.get("ieee_address"), inv.get("software_build_id"),
             inv.get("type"), inv.get("interview_completed")) !=
            (profile["ieee"], profile["postflash_build"], "Router", True)):
        raise ValueError("Fresh postflash identity and coordinator evidence absent")
    if (live.get("ieeeAddr"), live.get("swBuildId"), live.get("type"),
            live.get("modelId"), live.get("manufName"),
            live.get("interviewCompleted")) != (
            profile["ieee"], profile["postflash_build"], "Router",
            profile["model"], profile["manufacturer"], True):
        raise ValueError("Current actual device does not match the prior OTA")
    last = live.get("lastSeen")
    if not isinstance(last, (int, float)):
        raise ValueError("Last device communication absent")
    if not dt.timedelta(seconds=-30) <= (
            now - dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc)
    ) <= dt.timedelta(minutes=15):
        raise ValueError("Recent device presence not established")
    if not when(work["completed"]) <= when(post["observed_at"]) <= now:
        raise ValueError("Invalid OTA/postflash chronology")
    if now - when(post["observed_at"]) > dt.timedelta(hours=2):
        raise ValueError("Failed postflash evidence stale; refresh it")
    return {
        "result": FAILED_POSTFLASH_PHASE,
        "ieee": profile["ieee"], "device": profile["device"],
        "token": token, "image_sha256": profile["sha256"],
        "installed_build": profile["postflash_build"],
        "failure": FAILURE, "hardware_acceptance": False,
        "pm_energy_retention_accepted": False,
        "new_ota_authorized": False,
    }


def main():
    ap = argparse.ArgumentParser()
    for k in ("profile", "site", "postflash", "output",
              "confirm-ieee", "confirm-sha256"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import psutil
    for proc in psutil.process_iter(["name", "cmdline"]):
        if "python" not in (proc.info.get("name") or "").lower():
            continue
        parts = proc.info.get("cmdline") or []
        if any(Path(str(value)).name in (
                "bseed_targeted_z2m_ota.py", "bseed_ota_resume_supervisor.py")
                for value in parts):
            raise ValueError("Active OTA supervisor prevents terminalization")
    profile = load_profile(args.profile)
    if (args.confirm_ieee, args.confirm_sha256) != (
            profile["ieee"], profile["sha256"]):
        raise ValueError("Exact previous campaign IEEE and SHA required")
    work_path = Path(profile["workdir"]) / "ACTIVE_LOCK.json"
    network_path = network_lock_path(profile, required=True)
    work = json.loads(work_path.read_text())
    network = read_lock(network_path)
    post_path = Path(args.postflash).resolve()
    output = Path(args.output).resolve()
    if (not post_path.is_file() or post_path.is_relative_to(ROOT) or
            output.exists() or output.is_relative_to(ROOT)):
        raise ValueError("Require private immutable evidence and a new output")
    post = json.loads(post_path.read_text())
    site = load_site(args.site)
    live = live_identity(site, profile["ieee"])
    facts = validate(profile, work, network, post, live)
    if args.dry_run:
        print(json.dumps({"dry_run": True, **facts}, indent=2))
        return
    record = {
        **facts, "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "postflash_sha256": hashlib.sha256(post_path.read_bytes()).hexdigest(),
        "work_lock_sha256": hashlib.sha256(work_path.read_bytes()).hexdigest(),
        "network_released": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    work.update(phase=FAILED_POSTFLASH_PHASE, failed_acceptance_evidence=str(output),
                hardware_acceptance=False, pm_energy_retention_accepted=False)
    work_path.write_text(json.dumps(work, indent=2) + "\n")
    update(network_path, facts["token"], FAILED_POSTFLASH_PHASE,
           failed_acceptance_evidence=str(output), hardware_acceptance=False)
    release_failed_postflash(network_path, facts["token"])
    record["network_released"] = True
    output.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
