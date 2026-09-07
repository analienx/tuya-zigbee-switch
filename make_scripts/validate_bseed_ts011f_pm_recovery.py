#!/usr/bin/env python3
"""Strict software/build gate for the V8-lineage BSEED PM recovery image.

BUILD ONLY. The gate proves recovery-source isolation by building exact ded91a1
and this branch's ordinary V8 image inside the same Actions job, with the same
SDK/toolchain/date, and requiring BIN+OTA byte identity. It then produces
recovery v0x12053003 twice and requires byte-for-byte reproducibility. It never
publishes or flashes firmware.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCEPTED_V8_SHA = "ded91a1fb1cdeb320d0858c8f4bcabab32bf5564"
HISTORICAL_ACCEPTED_V8_OTA_SHA256 = "c3ccb484c28d7ef08594acc306b2054aed3ba9fcfc9579643da339f7fcc9fe7c"
RECOVERY_VERSION = 0x12053003
RECOVERY_VERSION_DEC = 302329859
MANUFACTURER = 4417
IMAGE_TYPE = 43556
CANONICAL = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"


def run(
    cmd: list[str], cwd: pathlib.Path = ROOT
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.returncode:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def digest(path: pathlib.Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    h.update(path.read_bytes())
    return h.hexdigest()


def require_clean(label: str) -> None:
    dirty = run(["git", "status", "--porcelain"]).stdout.strip()
    if dirty:
        raise SystemExit(f"{label}: working tree dirty:\n{dirty}")


def parse_ota(path: pathlib.Path) -> dict[str, int]:
    raw = path.read_bytes()
    if len(raw) < 56:
        raise SystemExit(f"OTA too short: {path}")
    values = struct.unpack("<I5HIH32sI", raw[:56])
    magic, hdr_version, hdr_len, field_ctrl, mfr, image_type, version, stack, _, total = values
    if magic != 0x0BEEF11E:
        raise SystemExit(f"bad OTA magic for {path}: 0x{magic:08x}")
    if total != len(raw):
        raise SystemExit(f"OTA size mismatch for {path}: header {total}, file {len(raw)}")
    return {
        "headerVersion": hdr_version,
        "headerLength": hdr_len,
        "fieldControl": field_ctrl,
        "manufacturerCode": mfr,
        "imageType": image_type,
        "fileVersion": version,
        "zigbeeStackVersion": stack,
        "totalImageSize": total,
    }


def require_byte_identical(label: str, left: pathlib.Path, right: pathlib.Path) -> None:
    if not left.is_file() or not right.is_file():
        raise SystemExit(f"{label}: missing comparison artifact")
    if left.read_bytes() != right.read_bytes():
        raise SystemExit(
            f"{label}: byte identity failed: "
            f"{digest(left)} != {digest(right)}"
        )


def build_exact_v8_base_same_job() -> pathlib.Path:
    """Build ded91a1 with this job's exact toolchain/date for isolation proof."""
    runner_temp = pathlib.Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    worktree = runner_temp / "bseed-v8-base-ded91a1"
    if worktree.exists():
        shutil.rmtree(worktree)

    run(["git", "worktree", "add", "--detach", str(worktree), ACCEPTED_V8_SHA])
    try:
        shared_tools = ROOT / "telink_tools"
        if not shared_tools.is_dir():
            raise SystemExit("same-job base comparison requires installed telink_tools")
        (worktree / "telink_tools").symlink_to(shared_tools, target_is_directory=True)

        out = worktree / "build/bseed-ts011f-pm-v8"
        run(
            [
                "bash",
                "make_scripts/build_bseed_ts011f_pm_v8.sh",
                str(out),
            ],
            cwd=worktree,
        )
        return worktree
    except BaseException:
        run(["git", "worktree", "remove", "--force", str(worktree)])
        raise


def main() -> int:
    for tool in ("git", "make", "bash", "python3"):
        if shutil.which(tool) is None:
            raise SystemExit(f"missing required tool: {tool}")

    require_clean("start")
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    # Full current V8 + TS0726 regression/toolchain contract first. This builds
    # ordinary PM V8 at 0x12053002 from the recovery branch with recovery mode
    # excluded by the compile-time fileVersion gate.
    run([sys.executable, "make_scripts/validate_bseed_ts011f_pm_v8.py"])

    branch_v8_dir = ROOT / "build/bseed-ts011f-pm-v8"
    branch_v8_bin = branch_v8_dir / "forward.bin"
    branch_v8_ota = branch_v8_dir / "forward.ota"
    if not branch_v8_bin.is_file() or not branch_v8_ota.is_file():
        raise SystemExit("ordinary V8 validator did not produce PM BIN+OTA")

    branch_v8_header = parse_ota(branch_v8_ota)
    if branch_v8_header["fileVersion"] != 0x12053002:
        raise SystemExit("ordinary branch V8 version drifted")
    if (
        branch_v8_header["manufacturerCode"] != MANUFACTURER
        or branch_v8_header["imageType"] != IMAGE_TYPE
    ):
        raise SystemExit("ordinary branch V8 OTA identity drifted")

    # The historical accepted artifact embeds __DATE__, so a whole-file hash
    # from a different calendar day is not a valid source-isolation invariant.
    # Instead build exact ded91a1 side-by-side under this same runner/toolchain/
    # day and demand exact BIN+OTA equality with the branch's ordinary V8 build.
    base_worktree = build_exact_v8_base_same_job()
    try:
        base_v8_dir = base_worktree / "build/bseed-ts011f-pm-v8"
        base_v8_bin = base_v8_dir / "forward.bin"
        base_v8_ota = base_v8_dir / "forward.ota"
        base_v8_header = parse_ota(base_v8_ota)
        if base_v8_header != branch_v8_header:
            raise SystemExit(
                f"same-job V8 OTA header drift: {base_v8_header} != {branch_v8_header}"
            )
        require_byte_identical(
            "same-job exact-base vs recovery-branch ordinary V8 BIN",
            base_v8_bin,
            branch_v8_bin,
        )
        require_byte_identical(
            "same-job exact-base vs recovery-branch ordinary V8 OTA",
            base_v8_ota,
            branch_v8_ota,
        )
        same_job_base_bin_sha = digest(base_v8_bin)
        same_job_base_ota_sha = digest(base_v8_ota)
    finally:
        run(["git", "worktree", "remove", "--force", str(base_worktree)])

    # Build recovery twice. Each builder invocation performs a clean real TC32
    # build, so equality proves deterministic output rather than object reuse.
    out_a = ROOT / "build/bseed-ts011f-pm-recovery-a"
    out_b = ROOT / "build/bseed-ts011f-pm-recovery-b"
    run(["bash", "make_scripts/build_bseed_ts011f_pm_recovery.sh", str(out_a)])
    run(["bash", "make_scripts/build_bseed_ts011f_pm_recovery.sh", str(out_b)])

    artifacts: dict[str, dict[str, object]] = {}
    for name in ("forward.bin", "forward.ota"):
        a = out_a / name
        b = out_b / name
        require_byte_identical(f"recovery clean-build reproducibility {name}", a, b)
        artifacts[name] = {
            "bytes": a.stat().st_size,
            "sha256": digest(a),
            "sha512": digest(a, "sha512"),
            "reproducedByteIdentical": True,
        }

    recovery_ota = out_a / "forward.ota"
    recovery_header = parse_ota(recovery_ota)
    expected_identity = (MANUFACTURER, IMAGE_TYPE, RECOVERY_VERSION_DEC)
    actual_identity = (
        recovery_header["manufacturerCode"],
        recovery_header["imageType"],
        recovery_header["fileVersion"],
    )
    if actual_identity != expected_identity:
        raise SystemExit(
            f"recovery OTA identity mismatch: {actual_identity} != {expected_identity}"
        )

    manifest = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
    for key, expected in {
        "sourceCommit": head,
        "sourceDirty": False,
        "acceptedV8BaseSha": ACCEPTED_V8_SHA,
        "acceptedV8OtaSha256": HISTORICAL_ACCEPTED_V8_OTA_SHA256,
        "board": "OUTLET_BSEED_PM_TS011F",
        "swBuildId": "1.2.5-bseed-pm-recovery1",
        "fileVersion": RECOVERY_VERSION_DEC,
        "manufacturerCode": MANUFACTURER,
        "imageType": IMAGE_TYPE,
        "canonicalConfig": CANONICAL,
    }.items():
        if manifest.get(key) != expected:
            raise SystemExit(
                f"recovery manifest mismatch {key}: "
                f"{manifest.get(key)!r} != {expected!r}"
            )

    semantics = manifest.get("recoverySemantics")
    if not isinstance(semantics, dict):
        raise SystemExit("missing recoverySemantics manifest section")
    if semantics.get("lowLoadSuppression") is not False:
        raise SystemExit("recovery low-load suppression must be disabled")
    for key in (
        "v8PlatformRetained",
        "v8NvmMigrationRetained",
        "v8OtaStackRetained",
    ):
        if semantics.get(key) is not True:
            raise SystemExit(f"recovery manifest missing retained invariant: {key}")

    binary = (out_a / "forward.bin").read_bytes()
    if CANONICAL.encode("ascii") not in binary:
        raise SystemExit("canonical PM config is absent from recovery binary")

    require_clean("end")

    result = {
        "status": "PASS",
        "sourceCommit": head,
        "acceptedV8BaseSha": ACCEPTED_V8_SHA,
        "historicalAcceptedV8OtaSha256": HISTORICAL_ACCEPTED_V8_OTA_SHA256,
        "historicalHashNotUsedAsCrossDayEqualityGate": True,
        "sameJobBaseV8BinSha256": same_job_base_bin_sha,
        "sameJobBaseV8OtaSha256": same_job_base_ota_sha,
        "branchOrdinaryV8BinSha256": digest(branch_v8_bin),
        "branchOrdinaryV8OtaSha256": digest(branch_v8_ota),
        "sameJobExactBaseVsBranchOrdinaryV8ByteIdentical": True,
        "recoveryVersion": RECOVERY_VERSION_DEC,
        "recoveryHeader": recovery_header,
        "recoveryArtifacts": artifacts,
        "recoveryBuildsByteIdentical": True,
        "canonicalConfigEmbedded": True,
        "note": "VALIDATE+BUILD only; no publication, config write, or device flash performed",
    }
    output = ROOT / "build/bseed-ts011f-pm-recovery-validation.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
