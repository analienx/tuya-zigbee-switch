#!/usr/bin/env python3
"""Strict software/build gate for the V8-lineage BSEED PM recovery image.

This validator is BUILD ONLY. It proves that normal V8 remains byte-identical to
the accepted ded91a1 OTA, then produces recovery v0x12053003 twice and requires
byte-for-byte reproducibility. It never publishes or flashes firmware.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCEPTED_V8_SHA = "ded91a1fb1cdeb320d0858c8f4bcabab32bf5564"
ACCEPTED_V8_OTA_SHA256 = "c3ccb484c28d7ef08594acc306b2054aed3ba9fcfc9579643da339f7fcc9fe7c"
RECOVERY_VERSION = 0x12053003
RECOVERY_VERSION_DEC = 302329859
MANUFACTURER = 4417
IMAGE_TYPE = 43556
CANONICAL = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def main() -> int:
    for tool in ("git", "make", "bash", "python3"):
        if shutil.which(tool) is None:
            raise SystemExit(f"missing required tool: {tool}")

    require_clean("start")
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    # First prove the full existing V8/TS0726 software contract from this exact
    # source SHA. This also builds ordinary PM V8 at 0x12053002.
    run([sys.executable, "make_scripts/validate_bseed_ts011f_pm_v8.py"])

    baseline_ota = ROOT / "build/bseed-ts011f-pm-v8/forward.ota"
    if not baseline_ota.is_file():
        raise SystemExit("ordinary V8 validator did not produce baseline OTA")
    baseline_sha = sha256(baseline_ota)
    if baseline_sha != ACCEPTED_V8_OTA_SHA256:
        raise SystemExit(
            "normal V8 artifact drifted on recovery branch: "
            f"{baseline_sha} != {ACCEPTED_V8_OTA_SHA256}"
        )
    baseline_header = parse_ota(baseline_ota)
    if baseline_header["fileVersion"] != 0x12053002:
        raise SystemExit("baseline V8 version drifted")
    if baseline_header["manufacturerCode"] != MANUFACTURER or baseline_header["imageType"] != IMAGE_TYPE:
        raise SystemExit("baseline V8 OTA identity drifted")

    # Build recovery twice. The builder performs a real clean TC32 build each
    # time; equality therefore proves deterministic output from the pinned
    # source/toolchain rather than reuse of object files.
    out_a = ROOT / "build/bseed-ts011f-pm-recovery-a"
    out_b = ROOT / "build/bseed-ts011f-pm-recovery-b"
    run(["bash", "make_scripts/build_bseed_ts011f_pm_recovery.sh", str(out_a)])
    run(["bash", "make_scripts/build_bseed_ts011f_pm_recovery.sh", str(out_b)])

    artifacts: dict[str, dict[str, object]] = {}
    for name in ("forward.bin", "forward.ota"):
        a = out_a / name
        b = out_b / name
        if not a.is_file() or not b.is_file():
            raise SystemExit(f"missing recovery artifact {name}")
        a_bytes = a.read_bytes()
        b_bytes = b.read_bytes()
        if a_bytes != b_bytes:
            raise SystemExit(f"recovery reproducibility failure: {name} differs between clean builds")
        artifacts[name] = {
            "bytes": len(a_bytes),
            "sha256": hashlib.sha256(a_bytes).hexdigest(),
            "sha512": hashlib.sha512(a_bytes).hexdigest(),
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
        raise SystemExit(f"recovery OTA identity mismatch: {actual_identity} != {expected_identity}")

    manifest = json.loads((out_a / "manifest.json").read_text(encoding="utf-8"))
    for key, expected in {
        "sourceCommit": head,
        "sourceDirty": False,
        "acceptedV8BaseSha": ACCEPTED_V8_SHA,
        "acceptedV8OtaSha256": ACCEPTED_V8_OTA_SHA256,
        "board": "OUTLET_BSEED_PM_TS011F",
        "swBuildId": "1.2.5-bseed-pm-recovery1",
        "fileVersion": RECOVERY_VERSION_DEC,
        "manufacturerCode": MANUFACTURER,
        "imageType": IMAGE_TYPE,
        "canonicalConfig": CANONICAL,
    }.items():
        if manifest.get(key) != expected:
            raise SystemExit(f"recovery manifest mismatch {key}: {manifest.get(key)!r} != {expected!r}")

    semantics = manifest.get("recoverySemantics")
    if not isinstance(semantics, dict):
        raise SystemExit("missing recoverySemantics manifest section")
    if semantics.get("lowLoadSuppression") is not False:
        raise SystemExit("recovery low-load suppression must be disabled")
    for key in ("v8PlatformRetained", "v8NvmMigrationRetained", "v8OtaStackRetained"):
        if semantics.get(key) is not True:
            raise SystemExit(f"recovery manifest missing retained invariant: {key}")

    # Ensure exact production config is embedded in the built binary. One or
    # more occurrences are acceptable because compiler/string pooling differs,
    # but absence is a hard blocker.
    binary = (out_a / "forward.bin").read_bytes()
    if CANONICAL.encode("ascii") not in binary:
        raise SystemExit("canonical PM config is absent from recovery binary")

    require_clean("end")

    result = {
        "status": "PASS",
        "sourceCommit": head,
        "acceptedV8BaseSha": ACCEPTED_V8_SHA,
        "baselineV8OtaSha256": baseline_sha,
        "baselineV8ByteIdentityPreserved": True,
        "recoveryVersion": RECOVERY_VERSION_DEC,
        "recoveryHeader": recovery_header,
        "recoveryArtifacts": artifacts,
        "recoveryBuildsByteIdentical": True,
        "canonicalConfigEmbedded": True,
        "note": "VALIDATE+BUILD only; no publication, config write, or device flash performed",
    }
    output = ROOT / "build/bseed-ts011f-pm-recovery-validation.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
