#!/usr/bin/env python3
"""End-to-end local validation for the BSEED TS0726 V8 hardening branch.

This validates host tests, identity policy and a reproducible Telink build.
It never publishes or flashes firmware.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V7_BASE = "0f54303a52aed85f985b8c3b08bcf03aa88efc2a"
FOCUSED = [
    "tests/test_nvm_migration_version.py",
    "tests/test_config_resource_guard.py",
    "tests/test_image_type_checker.py",
]


def run(cmd: list[str]) -> dict[str, object]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    result: dict[str, object] = {
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
    for tool in ("make", "git", "bash"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")

    build_script = ROOT / "make_scripts" / "build_bseed_ts0726_v8.sh"
    if b"\r\n" in build_script.read_bytes():
        raise SystemExit("build_bseed_ts0726_v8.sh contains CRLF")

    head = run(["git", "rev-parse", "HEAD"])["stdout"].strip()
    dirty = run(["git", "status", "--porcelain"])["stdout"].strip()
    if dirty:
        raise SystemExit("working tree must be clean before reproducibility validation")

    steps: list[dict[str, object]] = []
    steps.append(run(["make", "stub/build"]))
    steps.append(run(["make", "stub/build_end_device"]))
    steps.append(run([sys.executable, "-m", "pytest", *FOCUSED, "-q"]))
    steps.append(run([sys.executable, "-m", "pytest", "tests/", "-q"]))
    steps.append(
        run(
            [
                sys.executable,
                "helper_scripts/check_image_types.py",
                "device_db.yaml",
                "--changed-base",
                V7_BASE,
            ]
        )
    )
    steps.append(run(["bash", "make_scripts/build_bseed_ts0726_v8.sh"]))

    manifest_path = ROOT / "build" / "bseed-ts0726-v8" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "sourceCommit": head,
        "sourceDirty": False,
        "swBuildId": "1.1.8-bseedv8",
        "fileVersion": 285356042,
        "manufacturerCode": 4417,
        "imageType": 45577,
        "deviceConfigGuard": "BSEED_TS0726_3GANG",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(
                f"manifest mismatch for {key}: {manifest.get(key)!r} != {value!r}"
            )

    output = {
        "status": "PASS",
        "sourceCommit": head,
        "v7Base": V7_BASE,
        "focusedTests": FOCUSED,
        "manifest": str(manifest_path.relative_to(ROOT)),
        "artifacts": manifest["artifacts"],
        "steps": [
            {"command": item["command"], "exitCode": item["exitCode"]}
            for item in steps
        ],
        "note": "VALIDATE+BUILD only; no publication or device flash performed",
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
