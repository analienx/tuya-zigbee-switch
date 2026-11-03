"""Read-only proof that a later accepted install superseded old OTA worklocks.

Historical update_error/ota_running files must remain immutable evidence. They
are not active network leases, and a subsequent independently accepted image
can establish that a *specific* earlier campaign no longer owns the device.
Only enabled for explicitly pinned private successor evidence; fail closed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _private_json(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.is_relative_to(ROOT):
        raise ValueError("Historical acceptance proof must be a private file")
    return path, json.loads(path.read_text(encoding="utf8"))


def _date(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timezone required in historical campaign proof")
    return parsed.astimezone(dt.timezone.utc)


def accepted_successor(config, *, device, ieee, live):
    """Validate the accepted successor and its independently hashed artifacts."""
    if not isinstance(config, dict) or set(config) != {"lock", "acceptance"}:
        raise ValueError("Explicit accepted successor lock+acceptance paths required")
    _, lock = _private_json(config["lock"])
    _, evidence = _private_json(config["acceptance"])
    if (lock.get("phase"), lock.get("hardware_acceptance")) != (
            "postflash_accepted", True):
        raise ValueError("Successor is not independently hardware accepted")
    token = lock.get("token")
    build = (lock.get("response") or {}).get("data", {}).get("to", {}).get("software_build_id")
    if not (isinstance(token, str) and token and isinstance(build, str) and build):
        raise ValueError("Accepted successor transport identity missing")
    if (lock.get("device"), lock.get("ieee"), evidence.get("device"),
            evidence.get("ieee"), evidence.get("token"),
            evidence.get("image_sha256"), evidence.get("build"),
            evidence.get("result"), (lock.get("response") or {}).get("status"),
            (lock.get("response") or {}).get("transaction"),
            (lock.get("response") or {}).get("data", {}).get("id")) != (
            device, ieee, device, ieee, token, lock.get("sha256"), build,
            "postflash_accepted", "ok", token, ieee):
        raise ValueError("Accepted successor target/transport mismatch")
    if (live.get("ieeeAddr"), live.get("swBuildId"),
            live.get("interviewCompleted")) != (ieee, build, True):
        raise ValueError("Current installed source is not the accepted successor")
    completed = _date(lock["completed"])
    accepted = _date(lock["accepted_at"])
    if not (completed < accepted and accepted == _date(evidence["accepted_at"])):
        raise ValueError("Successor acceptance timestamp inconsistent")
    last_seen = live.get("lastSeen")
    if not isinstance(last_seen, (int, float)) or dt.datetime.fromtimestamp(
            last_seen / 1000, tz=dt.timezone.utc) < accepted:
        raise ValueError("No live observation since accepted successor")
    if float(evidence.get("soak_seconds") or 0) <= 0:
        raise ValueError("No successor soak evidence")
    for field in ("postflash", "soak"):
        path, _ = _private_json(evidence[field + "_evidence"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get(
                field + "_evidence_sha256"):
            raise ValueError("Successor acceptance artifact hash mismatch: " + field)
    return {
        "accepted_at": accepted,
        "device": device,
        "ieee": ieee,
        "build": build,
        "sha256": lock["sha256"],
        "token": token,
        "accepted_lock": str(config["lock"]),
    }


def historical_lock_superseded(old, proof):
    """Old local evidence is superseded, but never deleted or reclassified."""
    if not isinstance(old, dict) or not isinstance(proof, dict):
        return False
    if (old.get("device"), old.get("ieee")) != (proof["device"], proof["ieee"]):
        return False
    try:
        started = _date(old["started"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return started < proof["accepted_at"]
