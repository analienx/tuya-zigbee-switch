#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS011F non-PM Router build for the sacrificial client canary.
# BUILD ONLY: this script never publishes, flashes, writes device config, or
# updates an OTA index. SOURCE_ROOT may point at a detached worktree so CI can
# compare the branch against its exact main merge-base with the same harness.

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${SOURCE_ROOT:-$SCRIPT_ROOT}"
cd "$ROOT"

BOARD='OUTLET_BSEED_TS011F'
CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=43555
STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'
STOCK_IMAGE_TYPE=54179
SW_BUILD='1.1.2-bseednp-r1'
FILE_VERSION_HEX='0x11023000'
FILE_VERSION_DEC=285356032

OUT_DIR="${1:-build/bseed-ts011f-nonpm-canary-router}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"

python3 - "$BOARD" "$CANONICAL" "$IMAGE_TYPE" "$MANUFACTURER_CODE" \
    "$STOCK_MANUFACTURER_NAME" "$STOCK_IMAGE_TYPE" <<'PY'
import sys
import yaml

board, canonical, image_type, manufacturer, stock_name, stock_type = sys.argv[1:]
image_type = int(image_type)
manufacturer = int(manufacturer)
stock_type = int(stock_type)
with open("device_db.yaml", "r", encoding="utf-8") as f:
    db = yaml.safe_load(f)
entry = db[board]
assert entry["config_str"] == canonical, "canonical config drift"
assert int(entry["firmware_image_type"]) == image_type, "router image type drift"
assert int(entry["stock_manufacturer_id"]) == manufacturer, "manufacturer drift"
assert entry["stock_manufacturer_name"] == stock_name, "stock manufacturer drift"
assert int(entry["stock_image_type"]) == stock_type, "stock image type drift"
assert entry["device_type"] == "router", "validated target is no longer router"
assert entry["mcu_family"] == "Telink", "target is no longer Telink"
assert entry["mcu"] == "TLSR8258", "target MCU drift"
PY

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"
FROM_TUYA_OTA="$OUT_DIR/from_tuya.ota"

COMMON_ARGS=(
    VERSION_STR="$SW_BUILD"
    FILE_VERSION="$FILE_VERSION_HEX"
    NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
    DEVICE_TYPE=router
    CONFIG_STR="$CANONICAL"
    IMAGE_TYPE="$IMAGE_TYPE"
    MANUFACTURER_ID="$MANUFACTURER_CODE"
)

make -C src/telink clean
make -C src/telink build \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN"

make -C src/telink ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$IMAGE_TYPE" \
    OTA_VERSION="$FILE_VERSION_HEX"

# Canary-only stock Tuya -> custom Router wrapper. The Telink payload is exactly
# the normal Router payload; only the outer OTA identity/version is stock-facing.
make -C src/telink ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$FROM_TUYA_OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$STOCK_IMAGE_TYPE" \
    OTA_VERSION=0xFFFFFFFF

python3 - "$OUT_DIR" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" \
    "$MANUFACTURER_CODE" "$IMAGE_TYPE" "$STOCK_MANUFACTURER_NAME" \
    "$STOCK_IMAGE_TYPE" "$NVM_SCHEMA" "$CANONICAL" <<'PY'
from __future__ import annotations

import hashlib
import json
import pathlib
import struct
import subprocess
import sys

(
    out_dir,
    board,
    sw_build,
    file_version,
    manufacturer,
    image_type,
    stock_manufacturer_name,
    stock_image_type,
    nvm_schema,
    canonical,
) = sys.argv[1:]
out = pathlib.Path(out_dir)
file_version = int(file_version)
manufacturer = int(manufacturer)
image_type = int(image_type)
stock_image_type = int(stock_image_type)
nvm_schema = int(nvm_schema)

paths = [out / "forward.bin", out / "forward.ota", out / "from_tuya.ota"]
for path in paths:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing/empty artifact: {path}")


def header(path: pathlib.Path) -> dict[str, int]:
    values = struct.unpack("<I5HIH32sI", path.read_bytes()[:56])
    magic, hv, hl, fc, mfr, image, version, stack, _, total = values
    if magic != 0x0BEEF11E or total != path.stat().st_size:
        raise SystemExit(f"invalid OTA header: {path.name}")
    return {
        "headerVersion": hv,
        "headerLength": hl,
        "fieldControl": fc,
        "manufacturerCode": mfr,
        "imageType": image,
        "fileVersion": version,
        "zigbeeStackVersion": stack,
        "totalImageSize": total,
    }

normal = header(out / "forward.ota")
stock = header(out / "from_tuya.ota")
assert normal["manufacturerCode"] == manufacturer
assert normal["imageType"] == image_type
assert normal["fileVersion"] == file_version
assert stock["manufacturerCode"] == manufacturer
assert stock["imageType"] == stock_image_type
assert stock["fileVersion"] == 0xFFFFFFFF

normal_bytes = (out / "forward.ota").read_bytes()
stock_bytes = (out / "from_tuya.ota").read_bytes()
assert normal_bytes[56:] == stock_bytes[56:]
diffs = [i for i, (a, b) in enumerate(zip(normal_bytes, stock_bytes)) if a != b]
assert diffs == list(range(12, 18)), f"unexpected stock-wrapper diff offsets: {diffs}"

source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
manifest = {
    "schema": 1,
    "canaryOnly": True,
    "sourceCommit": source_commit,
    "sourceDirty": source_dirty,
    "board": board,
    "swBuildId": sw_build,
    "fileVersion": file_version,
    "manufacturerCode": manufacturer,
    "imageType": image_type,
    "canonicalConfig": canonical,
    "nvmMigrationsVersion": nvm_schema,
    "stockConversion": {
        "stockManufacturerName": stock_manufacturer_name,
        "stockManufacturerCode": manufacturer,
        "stockImageType": stock_image_type,
        "wrapperFileVersion": 0xFFFFFFFF,
        "payloadFromByte56Identical": True,
    },
    "distribution": {
        "normalOtaIndex": False,
        "sacrificialHardwareCanaryOnly": True,
    },
    "artifacts": {},
    "otaHeader": normal,
    "fromTuyaOtaHeader": stock,
    "note": "BUILD ONLY; intended first stage is stock -> Router before Router -> Client",
}
for path in paths:
    data = path.read_bytes()
    manifest["artifacts"][path.name] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
    }
(out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
