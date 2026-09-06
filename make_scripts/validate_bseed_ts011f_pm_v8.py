#!/usr/bin/env python3
"""Reproducibility gate for the unified BSEED TS011F-BS-PM V8 target.

The validator proves host regressions, PM NVM/cluster behavior, OTA identity,
a real Telink PM build, and a same-core TS0726 V8 regression build. It never
publishes or flashes firmware.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PM_INTEGRATION_BASE = "8ed8ddfcf5892f0b801d19df4882a145a42aa3b1"
FOCUSED = [
    "tests/test_unified_pm_v8.py",
    "tests/test_pm_cluster_layout_guard.py",
    "tests/test_bseed_pm_v8_release.py",
    "tests/test_nvm_migration_version.py",
    "tests/test_config_resource_guard.py",
    "tests/test_bseed_v6_binding_mode_release.py",
    "tests/test_bseed_config_guard_release.py",
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


def load_manifest(relative: str) -> dict[str, object]:
    path = ROOT / relative
    if not path.is_file():
        raise SystemExit(f"missing manifest: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def require_values(
    name: str, manifest: dict[str, object], expected: dict[str, object]
) -> None:
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(
                f"{name} manifest mismatch for {key}: "
                f"{manifest.get(key)!r} != {value!r}"
            )


def main() -> int:
    for tool in ("make", "git", "bash"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")

    pm_build = ROOT / "make_scripts" / "build_bseed_ts011f_pm_v8.sh"
    ts0726_build = ROOT / "make_scripts" / "build_bseed_ts0726_v8.sh"
    for script in (pm_build, ts0726_build):
        if b"\r\n" in script.read_bytes():
            raise SystemExit(f"{script.name} contains CRLF")

    head = str(run(["git", "rev-parse", "HEAD"])["stdout"]).strip()
    dirty = str(run(["git", "status", "--porcelain"])["stdout"]).strip()
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
                PM_INTEGRATION_BASE,
            ]
        )
    )

    # Build the new PM target first, then rebuild the accepted TS0726 V8 target
    # from the exact same source SHA to catch common-core/toolchain regressions.
    steps.append(run(["bash", "make_scripts/build_bseed_ts011f_pm_v8.sh"]))
    steps.append(run(["bash", "make_scripts/build_bseed_ts0726_v8.sh"]))

    pm_manifest_path = "build/bseed-ts011f-pm-v8/manifest.json"
    pm_manifest = load_manifest(pm_manifest_path)
    require_values(
        "PM",
        pm_manifest,
        {
            "sourceCommit": head,
            "sourceDirty": False,
            "board": "OUTLET_BSEED_PM_TS011F",
            "swBuildId": "1.2.5-bseedv8u1",
            "fileVersion": 302329858,
            "manufacturerCode": 4417,
            "imageType": 43556,
            "canonicalConfig": "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;",
        },
    )
    meter = pm_manifest.get("meter")
    if not isinstance(meter, dict):
        raise SystemExit("PM manifest missing meter object")
    for key, value in {
        "type": "BL0937",
        "cf": "PA1",
        "cf1": "PC2",
        "sel": "PB1",
        "voltageMultiplier": 161460,
        "currentMultiplier": 144679,
        "powerMultiplier": 16989,
        "protectionEnabled": True,
    }.items():
        if meter.get(key) != value:
            raise SystemExit(f"PM meter manifest mismatch for {key}")

    ts0726_manifest_path = "build/bseed-ts0726-v8/manifest.json"
    ts0726_manifest = load_manifest(ts0726_manifest_path)
    require_values(
        "TS0726",
        ts0726_manifest,
        {
            "sourceCommit": head,
            "sourceDirty": False,
            "swBuildId": "1.1.8-bseedv8",
            "fileVersion": 285356042,
            "manufacturerCode": 4417,
            "imageType": 45577,
            "deviceConfigGuard": "BSEED_TS0726_3GANG",
        },
    )

    final_dirty = str(run(["git", "status", "--porcelain"])["stdout"]).strip()
    if final_dirty:
        raise SystemExit(f"validation modified tracked/unignored files:\n{final_dirty}")

    output = {
        "status": "PASS",
        "sourceCommit": head,
        "integrationBase": PM_INTEGRATION_BASE,
        "focusedTests": FOCUSED,
        "pmManifest": pm_manifest_path,
        "pmArtifacts": pm_manifest["artifacts"],
        "ts0726Manifest": ts0726_manifest_path,
        "ts0726Artifacts": ts0726_manifest["artifacts"],
        "steps": [
            {"command": item["command"], "exitCode": item["exitCode"]}
            for item in steps
        ],
        "note": "VALIDATE+BUILD only; no publication, config write, or device flash performed",
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
