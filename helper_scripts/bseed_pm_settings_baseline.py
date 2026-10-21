"""Capture a private read-only PM settings baseline for one exact BSEED socket.

This helper exists for legacy PM Router sources whose Basic softwareBuildID is
not readable. It never writes Zigbee, MQTT, HA, or device state. Identity is
bound to exact IEEE, PM board metadata in the Z2M database, and expected role.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

from bseed_pm_provision import ROOT, pm_snapshot, read_db
from bseed_pm_role_audit import settings_snapshot


def capture(record, *, ieee, expect_role):
    if expect_role not in ("Router", "EndDevice"):
        raise ValueError("Explicit valid role required")
    if record.get("ieeeAddr") != ieee or record.get("type") != expect_role:
        raise ValueError("Persisted IEEE/role mismatch")
    if record.get("manufName") != "b28wrpvx" or record.get("modelID") != "TS011F-BS-PM":
        raise ValueError("Persisted record is not the exact BSEED PM board")
    settings = settings_snapshot(record)
    if not settings:
        raise ValueError("No known PM socket settings found for baseline")
    return {
        "at": dt.datetime.now().astimezone().isoformat(),
        "ieee": ieee,
        "role": expect_role,
        "source_swBuildId": record.get("swBuildId"),
        "settings_snapshot": settings,
        "cached_pm": pm_snapshot(record),
        "writes": 0,
        "result": "read_only_settings_baseline",
    }


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ieee", required=True)
    p.add_argument("--expect-role", required=True)
    p.add_argument("--ssh-host", required=True)
    p.add_argument("--ssh-key", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--ssh-user", default="root")
    p.add_argument("--database", default="/config/zigbee2mqtt/database.db")
    return p.parse_args()


def main():
    a = arguments()
    if not a.ieee.startswith("0x") or len(a.ieee) != 18:
        raise ValueError("Exact IEEE required")
    output = Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Private unused evidence path outside repo required")
    key = Path(a.ssh_key).expanduser().resolve()
    if not key.is_file():
        raise ValueError("Private SSH key missing")
    record = read_db(a.ssh_host, a.ssh_user, key, a.database, a.ieee)
    evidence = capture(record, ieee=a.ieee, expect_role=a.expect_role)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf8")
    print(
        "PRIVATE_BASELINE", str(output),
        "RESULT", evidence["result"],
        "ROLE", evidence["role"],
        "SETTINGS_GROUPS", len(evidence["settings_snapshot"]),
        flush=True,
    )


if __name__ == "__main__":
    main()
