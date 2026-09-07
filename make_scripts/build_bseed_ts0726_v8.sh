#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS0726-3-BS V8 hardening build.
# BUILD ONLY: this script never publishes or flashes firmware.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CANONICAL='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;'
SWAPPED='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;'
BOARD='SWITCH_BSEED_TS0726_3GANG'
MANUFACTURER_CODE=4417
IMAGE_TYPE=45577
STOCK_MANUFACTURER_NAME='_TZ3002_iedhxgyi'
STOCK_IMAGE_TYPE=54179
SW_BUILD='1.1.8-bseedv8'
FILE_VERSION_HEX='0x1102300A'
FILE_VERSION_DEC=285356042

OUT_DIR="${1:-build/bseed-ts0726-v8}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"

readarray -t db_values < <(python3 - "$BOARD" <<'PY'
import sys
import yaml

board = sys.argv[1]
with open("device_db.yaml", "r", encoding="utf-8") as f:
    db = yaml.safe_load(f)
entry = db[board]
print(entry["config_str"])
print(entry["firmware_image_type"])
print(entry["stock_manufacturer_id"])
print(entry["stock_image_type"])
print(entry["stock_manufacturer_name"])
print(entry["device_type"])
print(entry["mcu_family"])
print(entry["mcu"])
PY
)

db_config="${db_values[0]}"
db_image_type="${db_values[1]}"
db_manufacturer="${db_values[2]}"
db_stock_image_type="${db_values[3]}"
db_stock_manufacturer_name="${db_values[4]}"
db_device_type="${db_values[5]}"
db_mcu_family="${db_values[6]}"
db_mcu="${db_values[7]}"

[[ "$db_config" == "$CANONICAL" ]] || {
    echo "ERROR: device_db canonical config drifted" >&2
    echo "expected: $CANONICAL" >&2
    echo "actual:   $db_config" >&2
    exit 2
}
[[ "$db_image_type" == "$IMAGE_TYPE" ]] || {
    echo "ERROR: BSEED image type drifted: expected $IMAGE_TYPE, got $db_image_type" >&2
    exit 2
}
[[ "$db_manufacturer" == "$MANUFACTURER_CODE" ]] || {
    echo "ERROR: manufacturer code drifted: expected $MANUFACTURER_CODE, got $db_manufacturer" >&2
    exit 2
}
[[ "$db_stock_image_type" == "$STOCK_IMAGE_TYPE" ]] || {
    echo "ERROR: stock image type drifted: expected $STOCK_IMAGE_TYPE, got $db_stock_image_type" >&2
    exit 2
}
[[ "$db_stock_manufacturer_name" == "$STOCK_MANUFACTURER_NAME" ]] || {
    echo "ERROR: stock manufacturer drifted: expected $STOCK_MANUFACTURER_NAME, got $db_stock_manufacturer_name" >&2
    exit 2
}
[[ "$db_device_type" == "router" ]] || {
    echo "ERROR: BSEED target is no longer router" >&2
    exit 2
}
[[ "$db_mcu_family" == "Telink" ]] || {
    echo "ERROR: BSEED target is no longer Telink" >&2
    exit 2
}
[[ "$db_mcu" == "TLSR8258" ]] || {
    echo "ERROR: BSEED MCU drifted: expected TLSR8258, got $db_mcu" >&2
    exit 2
}

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
    MIGRATION_FROM_CONFIG="$SWAPPED"
    DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG
)

make -C src/telink clean
make -C src/telink build \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN"

# Normal custom -> custom OTA identity.
make -C src/telink ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$IMAGE_TYPE" \
    OTA_VERSION="$FILE_VERSION_HEX"

# Stock Tuya -> custom conversion wrapper around the exact same compiled BIN.
make -C src/telink ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$FROM_TUYA_OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$STOCK_IMAGE_TYPE" \
    OTA_VERSION=0xFFFFFFFF

python3 - "$OUT_DIR" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" \
    "$MANUFACTURER_CODE" "$IMAGE_TYPE" "$STOCK_MANUFACTURER_NAME" \
    "$STOCK_IMAGE_TYPE" "$NVM_SCHEMA" "$CANONICAL" "$SWAPPED" <<'PY'
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
    swapped,
) = sys.argv[1:]
out = pathlib.Path(out_dir)
file_version = int(file_version)
manufacturer = int(manufacturer)
image_type = int(image_type)
stock_image_type = int(stock_image_type)
nvm_schema = int(nvm_schema)

bin_path = out / "forward.bin"
ota_path = out / "forward.ota"
from_tuya_path = out / "from_tuya.ota"
for path in (bin_path, ota_path, from_tuya_path):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing/empty artifact: {path}")


def parse_header(path: pathlib.Path) -> dict[str, int]:
    header = struct.unpack("<I5HIH32sI", path.read_bytes()[:56])
    magic, hdr_version, hdr_len, field_ctrl, ota_mfr, ota_type, ota_version, stack_ver, _, total = header
    if magic != 0x0BEEF11E:
        raise SystemExit(f"bad OTA magic in {path.name}: 0x{magic:08x}")
    if total != path.stat().st_size:
        raise SystemExit(f"OTA total_image_size mismatch in {path.name}: {total} != {path.stat().st_size}")
    return {
        "headerVersion": hdr_version,
        "headerLength": hdr_len,
        "fieldControl": field_ctrl,
        "manufacturerCode": ota_mfr,
        "imageType": ota_type,
        "fileVersion": ota_version,
        "zigbeeStackVersion": stack_ver,
        "totalImageSize": total,
    }


normal_header = parse_header(ota_path)
stock_header = parse_header(from_tuya_path)
if normal_header["manufacturerCode"] != manufacturer:
    raise SystemExit("normal OTA manufacturer mismatch")
if normal_header["imageType"] != image_type:
    raise SystemExit("normal OTA image type mismatch")
if normal_header["fileVersion"] != file_version:
    raise SystemExit("normal OTA file version mismatch")
if stock_header["manufacturerCode"] != manufacturer:
    raise SystemExit("from-Tuya OTA manufacturer mismatch")
if stock_header["imageType"] != stock_image_type:
    raise SystemExit("from-Tuya OTA stock image type mismatch")
if stock_header["fileVersion"] != 0xFFFFFFFF:
    raise SystemExit("from-Tuya OTA version must be 0xFFFFFFFF")

normal_bytes = ota_path.read_bytes()
stock_bytes = from_tuya_path.read_bytes()
if len(normal_bytes) != len(stock_bytes):
    raise SystemExit("normal/from-Tuya OTA sizes differ")
if normal_bytes[56:] != stock_bytes[56:]:
    raise SystemExit("from-Tuya wrapper changed bytes after the 56-byte OTA header")
diff_offsets = [
    i for i, (normal, stock) in enumerate(zip(normal_bytes, stock_bytes))
    if normal != stock
]
expected_offsets = list(range(12, 18))
if diff_offsets != expected_offsets:
    raise SystemExit(
        f"unexpected from-Tuya wrapper diff offsets: {diff_offsets}; expected {expected_offsets}"
    )

source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())

manifest = {
    "schema": 3,
    "sourceCommit": source_commit,
    "sourceDirty": source_dirty,
    "board": board,
    "swBuildId": sw_build,
    "fileVersion": file_version,
    "manufacturerCode": manufacturer,
    "imageType": image_type,
    "nvmMigrationsVersion": nvm_schema,
    "canonicalConfig": canonical,
    "migrationFromConfig": swapped,
    "deviceConfigGuard": "BSEED_TS0726_3GANG",
    "stockConversion": {
        "stockManufacturerName": stock_manufacturer_name,
        "stockManufacturerCode": manufacturer,
        "stockImageType": stock_image_type,
        "wrapperFileVersion": 0xFFFFFFFF,
        "headerDiffOffsets": diff_offsets,
        "payloadFromByte56Identical": True,
    },
    "artifacts": {},
    "otaHeader": normal_header,
    "fromTuyaOtaHeader": stock_header,
    "note": "BUILD ONLY; no publication or device flash performed",
}

for path in (bin_path, ota_path, from_tuya_path):
    data = path.read_bytes()
    manifest["artifacts"][path.name] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
    }

(out / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
