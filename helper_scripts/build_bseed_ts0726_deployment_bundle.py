#!/usr/bin/env python3
"""Build (but never deploy) the isolated BSEED TS0726 canary converter bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HISTORICAL_SHA256 = "ef79acfd2141837b539189bfadda07799b53267bd746e1209335d38b91c66bfe"
TARGET_DB_KEY = "SWITCH_BSEED_TS0726_3GANG"
AUTHORITATIVE_OVERLAY = Path("zigbee2mqtt/converters/bseed_ts0726_v4.js")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("historical_converter", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    historical = args.historical_converter.resolve()
    if not historical.is_file():
        raise SystemExit(f"historical converter not found: {historical}")
    historical_hash = sha256(historical)
    if historical_hash != HISTORICAL_SHA256:
        raise SystemExit(
            "refusing non-authoritative fleet baseline: "
            f"expected {HISTORICAL_SHA256}, got {historical_hash}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fleet = args.output_dir / "00-switch_custom-historical.js"
    overlay = args.output_dir / "10-bseed-ts0726-overlay.js"
    shutil.copyfile(historical, fleet)

    if not AUTHORITATIVE_OVERLAY.is_file():
        raise SystemExit(f"authoritative target overlay missing: {AUTHORITATIVE_OVERLAY}")
    shutil.copyfile(AUTHORITATIVE_OVERLAY, overlay)

    audit = run(
        sys.executable,
        "helper_scripts/audit_bseed_ts0726_v4_overlay.py",
        str(overlay),
    )
    syntax = run("node", "--check", str(overlay))
    action_probe = run(
        "node",
        "helper_scripts/probe_bseed_ts0726_action_contract.js",
        str(overlay),
    )

    manifest = {
        "status": "BUILT_NOT_DEPLOYED",
        "architecture": (
            "exact historical fleet converter + authoritative firmware-scoped "
            "BSEED v4 target overlay"
        ),
        "historical": {
            "file": fleet.name,
            "sha256": sha256(fleet),
            "bytes": fleet.stat().st_size,
            "required_sha256": HISTORICAL_SHA256,
        },
        "overlay": {
            "file": overlay.name,
            "sha256": sha256(overlay),
            "bytes": overlay.stat().st_size,
            "db_key": TARGET_DB_KEY,
            "fingerprint": {
                "manufacturerName": "iedhxgyi",
                "modelID": "TS0726-3-BS",
                "softwareBuildID": "1.1.4-bseedv4",
                "priority": 100,
            },
            "model": "EC-GL86ZPCS31",
        },
        "checks": {
            "overlay_audit": json.loads(audit.stdout),
            "javascript_syntax": "PASS" if syntax.returncode == 0 else "FAIL",
            "action_contract": json.loads(action_probe.stdout),
            "runtime_match_probe_required": True,
            "installed_zhc_mutation_probe_required": True,
        },
        "deployment": {
            "automatic": False,
            "requires_controlled_restart": True,
            "actual_ota_included": False,
        },
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
