#!/usr/bin/env python3
"""Strict build/reproducibility gate for BSEED PM recovery1.

This validator is intentionally build-only. It proves that:
* the recovery branch leaves the V8 Telink/OTA/Zigbee platform sources intact;
* the historical accepted V8 control artifact can be reproduced by the same
  GitHub runner/toolchain;
* recovery1 is a deterministic V8-lineage 0x12053003 image;
* TS0726 still builds and passes the shared-core regressions;
* no publication, config write, or live-device action occurs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V8_BASE = "ded91a1fb1cdeb320d0858c8f4bcabab32bf5564"
KNOWN_GOOD_PM = "8b8cc4924a353b35880666f7b48f0afbee89eb17"
CONTROL_OTA_SHA256 = "c3ccb484c28d7ef08594acc306b2054aed3ba9fcfc9579643da339f7fcc9fe7c"
CONTROL_OTA_BYTES = 195394
RECOVERY_FILE_VERSION = 302329859
RECOVERY_BUILD = "1.2.5-bseed-pm-recovery1"
CANONICAL = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"

ALLOWED_DIFF = {
    ".github/workflows/pm-recovery-reproducibility.yml",
    "make_scripts/build_bseed_ts011f_pm_recovery1.sh",
    "make_scripts/validate_bseed_ts011f_pm_recovery1.py",
    "src/base_components/energy_measurement/hlw8012.h",
    "tests/test_unified_pm_v8.py",
}

# These sources define the currently running V8 OTA/platform envelope and the
# PM plumbing around the single recovery constant. They must remain source-
# identical to the installed V8 candidate.
CRITICAL_UNCHANGED = [
    "src/app.c",
    "src/base_components/energy_measurement/hlw8012.c",
    "src/device_config/config_parser.c",
    "src/device_config/pm_legacy_migration.c",
    "src/telink/Makefile",
    "src/telink/hal/gpio_counter.c",
    "src/telink/hal/zigbee_ota.c",
    "src/telink/make_ota.py",
    "src/zigbee/electrical_measurement_cluster.c",
    "src/zigbee/metering_cluster.c",
]

FOCUSED = [
    "tests/test_unified_pm_v8.py",
    "tests/test_pm_cluster_layout_guard.py",
    "tests/test_bseed_pm_v8_release.py",
    "tests/test_pm_telink_repro_contract.py",
    "tests/test_nvm_migration_version.py",
    "tests/test_config_resource_guard.py",
    "tests/test_bseed_v6_binding_mode_release.py",
    "tests/test_bseed_config_guard_release.py",
    "tests/test_image_type_checker.py",
]


def run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "command": cmd,
                    "cwd": str(cwd),
                    "exitCode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                },
                indent=2,
            )
        )
        raise SystemExit(proc.returncode)
    return proc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SystemExit(f"missing manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def require_clean(repo: Path = ROOT) -> None:
    dirty = run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
    if dirty:
        raise SystemExit(f"working tree is not clean:\n{dirty}")


def require_source_boundary(head: str) -> list[str]:
    changed = [
        line
        for line in run(["git", "diff", "--name-only", V8_BASE, head]).stdout.splitlines()
        if line
    ]
    unexpected = sorted(set(changed) - ALLOWED_DIFF)
    if unexpected:
        raise SystemExit(f"unexpected recovery-branch source drift: {unexpected}")

    for path in CRITICAL_UNCHANGED:
        proc = run(["git", "diff", "--quiet", V8_BASE, "--", path], check=False)
        if proc.returncode != 0:
            raise SystemExit(f"critical V8 platform source changed: {path}")

    header = (ROOT / "src/base_components/energy_measurement/hlw8012.h").read_text(
        encoding="utf-8"
    )
    required = [
        "#ifdef BSEED_PM_B28WRPVX",
        "HLW8012_NO_LOAD_POWER_W              (-1)",
        "#else",
        "HLW8012_NO_LOAD_POWER_W              2",
        "HLW8012_NO_LOAD_CURRENT_MA           50",
        "HLW8012_NO_LOAD_CONFIRM_SAMPLES      3",
    ]
    for marker in required:
        if marker not in header:
            raise SystemExit(f"missing recovery PM contract marker: {marker}")
    return changed


def validate_manifest(manifest: dict[str, object], head: str) -> None:
    expected = {
        "sourceCommit": head,
        "sourceDirty": False,
        "board": "OUTLET_BSEED_PM_TS011F",
        "swBuildId": RECOVERY_BUILD,
        "fileVersion": RECOVERY_FILE_VERSION,
        "innerFileVersion": RECOVERY_FILE_VERSION,
        "manufacturerCode": 4417,
        "imageType": 43556,
        "canonicalConfig": CANONICAL,
        "v8PlatformBaseCommit": V8_BASE,
        "knownGoodPmBehaviorCommit": KNOWN_GOOD_PM,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise SystemExit(
                f"recovery manifest mismatch for {key}: {manifest.get(key)!r} != {value!r}"
            )

    contract = manifest.get("recoveryContract")
    if not isinstance(contract, dict):
        raise SystemExit("recovery manifest missing recoveryContract")
    if contract.get("noLoadSuppression") is not False:
        raise SystemExit("recovery must disable V8 low-load suppression")
    if contract.get("liveMutationAuthorized") is not False:
        raise SystemExit("build manifest must not authorize live mutation")

    meter = manifest.get("meter")
    if not isinstance(meter, dict):
        raise SystemExit("recovery manifest missing meter")
    for key, value in {
        "cf": "PA1",
        "cf1": "PC2",
        "sel": "PB1",
        "voltageMultiplier": 161460,
        "currentMultiplier": 144679,
        "powerMultiplier": 16989,
        "protectionEnabled": True,
    }.items():
        if meter.get(key) != value:
            raise SystemExit(f"recovery meter mismatch for {key}")


def build_control(temp_root: Path) -> dict[str, object]:
    control_repo = temp_root / "v8-control"
    control_out = temp_root / "v8-control-out"
    run(["git", "worktree", "add", "--detach", str(control_repo), V8_BASE])
    try:
        tools = ROOT / "telink_tools"
        if not tools.is_dir():
            raise SystemExit("telink_tools missing; install pinned SDK/toolchain before validation")
        (control_repo / "telink_tools").symlink_to(tools, target_is_directory=True)
        require_clean(control_repo)
        run(
            ["bash", "make_scripts/build_bseed_ts011f_pm_v8.sh", str(control_out)],
            cwd=control_repo,
        )
        ota = control_out / "forward.ota"
        manifest = load_manifest(control_out / "manifest.json")
        actual_sha = sha256(ota)
        if ota.stat().st_size != CONTROL_OTA_BYTES:
            raise SystemExit(
                f"V8 control size mismatch: {ota.stat().st_size} != {CONTROL_OTA_BYTES}"
            )
        if actual_sha != CONTROL_OTA_SHA256:
            raise SystemExit(
                f"V8 control SHA mismatch: {actual_sha} != {CONTROL_OTA_SHA256}"
            )
        if manifest.get("sourceCommit") != V8_BASE or manifest.get("sourceDirty") is not False:
            raise SystemExit("V8 control provenance mismatch")
        return {
            "sourceCommit": V8_BASE,
            "otaBytes": ota.stat().st_size,
            "otaSha256": actual_sha,
            "fileVersion": manifest.get("fileVersion"),
        }
    finally:
        run(["git", "worktree", "remove", "--force", str(control_repo)], check=False)


def build_recovery_twice(head: str, temp_root: Path) -> dict[str, object]:
    outputs = []
    for name in ("recovery-a", "recovery-b"):
        out = temp_root / name
        run(["bash", "make_scripts/build_bseed_ts011f_pm_recovery1.sh", str(out)])
        manifest = load_manifest(out / "manifest.json")
        validate_manifest(manifest, head)
        outputs.append((out, manifest))

    a, b = outputs
    for filename in ("forward.bin", "forward.ota", "manifest.json"):
        sha_a = sha256(a[0] / filename)
        sha_b = sha256(b[0] / filename)
        if sha_a != sha_b:
            raise SystemExit(
                f"recovery reproducibility failure for {filename}: {sha_a} != {sha_b}"
            )

    ota = a[0] / "forward.ota"
    if sha256(ota) == CONTROL_OTA_SHA256:
        raise SystemExit("recovery OTA unexpectedly equals installed V8 control bytes")
    return {
        "fileVersion": RECOVERY_FILE_VERSION,
        "swBuildId": RECOVERY_BUILD,
        "otaBytes": ota.stat().st_size,
        "otaSha256": sha256(ota),
        "binSha256": sha256(a[0] / "forward.bin"),
        "manifestSha256": sha256(a[0] / "manifest.json"),
        "replicaOtaSha256": sha256(b[0] / "forward.ota"),
        "evidenceDirectories": [str(a[0]), str(b[0])],
    }


def build_ts0726(temp_root: Path, head: str) -> dict[str, object]:
    out = temp_root / "ts0726-control"
    run(["bash", "make_scripts/build_bseed_ts0726_v8.sh", str(out)])
    manifest = load_manifest(out / "manifest.json")
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
            raise SystemExit(f"TS0726 regression manifest mismatch for {key}")
    return {
        "otaSha256": sha256(out / "forward.ota"),
        "binSha256": sha256(out / "forward.bin"),
        "fileVersion": manifest.get("fileVersion"),
    }


def main() -> int:
    for tool in ("make", "git", "bash", "python3"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")

    require_clean()
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    changed = require_source_boundary(head)

    # Host/static regressions first; do not spend the real TC32 build if these fail.
    run(["make", "stub/build"])
    run(["make", "stub/build_end_device"])
    run([sys.executable, "-m", "pytest", *FOCUSED, "-q"])
    run([sys.executable, "-m", "pytest", "tests/", "-q"])

    temp_parent = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(prefix="bseed-pm-recovery1-", dir=temp_parent) as td:
        temp_root = Path(td)
        control = build_control(temp_root)
        recovery = build_recovery_twice(head, temp_root)
        ts0726 = build_ts0726(temp_root, head)

        # Persist only immutable build evidence under ignored build/ for Actions upload.
        evidence = ROOT / "build" / "bseed-pm-recovery1-evidence"
        if evidence.exists():
            shutil.rmtree(evidence)
        evidence.mkdir(parents=True)
        for src_name, dst_name in (
            ("recovery-a", "recovery-a"),
            ("recovery-b", "recovery-b"),
        ):
            shutil.copytree(temp_root / src_name, evidence / dst_name)
        (evidence / "validation-summary.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "sourceCommit": head,
                    "v8BaseCommit": V8_BASE,
                    "knownGoodPmBehaviorCommit": KNOWN_GOOD_PM,
                    "changedFiles": changed,
                    "criticalPlatformSourcesUnchanged": CRITICAL_UNCHANGED,
                    "v8Control": control,
                    "recovery": recovery,
                    "ts0726Regression": ts0726,
                    "note": "BUILD/VALIDATION ONLY; no live-device mutation authorized",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    require_clean()
    print((ROOT / "build/bseed-pm-recovery1-evidence/validation-summary.json").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
