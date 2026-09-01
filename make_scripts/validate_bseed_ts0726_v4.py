#!/usr/bin/env python3
"""Reproducible local validation for the BSEED TS0726 v4 migration firmware.

This script performs BUILD + TEST only. It never publishes or flashes OTA.
It builds the host stub before running migration tests so a fresh checkout
cannot silently turn the suite into skips/errors because build/stub is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOCUSED = [
    "tests/test_device_migration.py",
    "tests/test_cross_image_migration.py",
]


def run(cmd: list[str]) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    result = {
        "command": cmd,
        "exitCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode != 0:
        print(json.dumps({"status": "FAIL", "failed": result}, indent=2))
        raise SystemExit(proc.returncode)
    return result


def main() -> int:
    if shutil.which("make") is None:
        raise SystemExit("make is required for stub validation")

    build_script = ROOT / "make_scripts" / "build_bseed_ts0726_v4.sh"
    if b"\r\n" in build_script.read_bytes():
        raise SystemExit(
            "build_bseed_ts0726_v4.sh contains CRLF; checkout must honor .gitattributes"
        )

    results = []
    results.append(run(["make", "stub/build"]))
    results.append(run(["make", "stub/build_end_device"]))
    results.append(run([sys.executable, "-m", "pytest", *FOCUSED, "-q"]))
    results.append(run([sys.executable, "-m", "pytest", "-q"]))

    head = run(["git", "rev-parse", "HEAD"])
    output = {
        "status": "PASS",
        "sourceCommit": head["stdout"].strip(),
        "focusedTests": FOCUSED,
        "steps": [
            {
                "command": item["command"],
                "exitCode": item["exitCode"],
            }
            for item in results
        ],
        "note": "BUILD+TEST only; no OTA publication or flash performed",
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
