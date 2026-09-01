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

    generated = run(
        sys.executable,
        "helper_scripts/make_z2m_custom_converters.py",
        "device_db.yaml",
        "--only-db-key",
        TARGET_DB_KEY,
    )
    overlay.write_text(generated.stdout, encoding="utf-8")

    audit = run(
        sys.executable,
        "helper_scripts/audit_bseed_ts0726_overlay.py",
        str(overlay),
    )
    action_probe_syntax = run(
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "p=Path('helper_scripts/probe_bseed_ts0726_action_contract.js'); "
            "assert p.exists() and p.stat().st_size > 0"
        ),
    )

    manifest = {
        "status": "BUILT_NOT_DEPLOYED",
        "architecture": "historical fleet converter + exact-fingerprint target overlay",
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
            },
            "model": "EC-GL86ZPCS31",
        },
        "checks": {
            "overlay_audit": json.loads(audit.stdout),
            "action_probe_present": action_probe_syntax.returncode == 0,
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
