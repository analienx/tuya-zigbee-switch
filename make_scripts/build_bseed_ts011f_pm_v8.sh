#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS011F-BS-PM unified-V8 build.
# BUILD ONLY: this script never publishes, flashes, writes device config, or
# performs any live-device action.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BOARD='OUTLET_BSEED_PM_TS011F_b28wrpvx'
CANONICAL='b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=43556
SW_BUILD='1.2.5-bseedv8u1'
# Hardware-proven predecessor is 0x12053001 / 302329857. Use the immediately
# following normal version so an accepted PM unit can take this image without
# relying on a forced 0xffffffff OTA wrapper.
FILE_VERSION_HEX='0x12053002'
FILE_VERSION_DEC=302329858
VOLTAGE_MULTIPLIER=161460
CURRENT_MULTIPLIER=144679
POWER_MULTIPLIER=16989

OUT_DIR="${1:-build/bseed-ts011f-pm-v8}"
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
print(entry["device_type"])
print(entry["mcu_family"])
print(entry["mcu"])
PY
)

db_config="${db_values[0]}"
db_image_type="${db_values[1]}"
db_manufacturer="${db_values[2]}"
db_device_type="${db_values[3]}"
db_mcu_family="${db_values[4]}"
db_mcu="${db_values[5]}"

[[ "$db_config" == "$CANONICAL" ]] || {
    echo "ERROR: PM device_db canonical config drifted" >&2
    echo "expected: $CANONICAL" >&2
    echo "actual:   $db_config" >&2
    exit 2
}
[[ "$db_image_type" == "$IMAGE_TYPE" ]] || {
    echo "ERROR: PM image type drifted: expected $IMAGE_TYPE, got $db_image_type" >&2
    exit 2
}
[[ "$db_manufacturer" == "$MANUFACTURER_CODE" ]] || {
    echo "ERROR: PM manufacturer code drifted: expected $MANUFACTURER_CODE, got $db_manufacturer" >&2
    exit 2
}
[[ "$db_device_type" == "router" ]] || {
    echo "ERROR: PM target is no longer router" >&2
    exit 2
}
[[ "$db_mcu_family" == "Telink" ]] || {
    echo "ERROR: PM target is no longer Telink" >&2
    exit 2
}
[[ "$db_mcu" == "TLSR8258" ]] || {
    echo "ERROR: PM MCU drifted: expected TLSR8258, got $db_mcu" >&2
    exit 2
}

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"

COMMON_ARGS=(
    VERSION_STR="$SW_BUILD"
    FILE_VERSION="$FILE_VERSION_HEX"
    NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
    DEVICE_TYPE=router
    CONFIG_STR="$CANONICAL"
    IMAGE_TYPE="$IMAGE_TYPE"
    MANUFACTURER_ID="$MANUFACTURER_CODE"
    BSEED_PM_B28WRPVX=1
    BSEED_PM_B28WRPVX_PROTECTION=1
    HLW8012_VOLTAGE_MULTIPLIER="$VOLTAGE_MULTIPLIER"
    HLW8012_CURRENT_MULTIPLIER="$CURRENT_MULTIPLIER"
    HLW8012_POWER_MULTIPLIER="$POWER_MULTIPLIER"
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

python3 - "$OUT_DIR" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" \
    "$MANUFACTURER_CODE" "$IMAGE_TYPE" "$NVM_SCHEMA" "$CANONICAL" \
    "$VOLTAGE_MULTIPLIER" "$CURRENT_MULTIPLIER" "$POWER_MULTIPLIER" <<'PY'
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
    nvm_schema,
    canonical,
    voltage_multiplier,
    current_multiplier,
    power_multiplier,
) = sys.argv[1:]
out = pathlib.Path(out_dir)
file_version = int(file_version)
manufacturer = int(manufacturer)
image_type = int(image_type)
nvm_schema = int(nvm_schema)
voltage_multiplier = int(voltage_multiplier)
current_multiplier = int(current_multiplier)
power_multiplier = int(power_multiplier)

bin_path = out / "forward.bin"
ota_path = out / "forward.ota"
for path in (bin_path, ota_path):
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing/empty artifact: {path}")

# Zigbee OTA header: <I5HIH32sI (56 bytes).
header = struct.unpack("<I5HIH32sI", ota_path.read_bytes()[:56])
(
    magic,
    hdr_version,
    hdr_len,
    field_ctrl,
    ota_mfr,
    ota_type,
    ota_version,
    stack_ver,
    _,
    total,
) = header
if magic != 0x0BEEF11E:
    raise SystemExit(f"bad OTA magic: 0x{magic:08x}")
if ota_mfr != manufacturer:
    raise SystemExit(f"OTA manufacturer mismatch: {ota_mfr} != {manufacturer}")
if ota_type != image_type:
    raise SystemExit(f"OTA image type mismatch: {ota_type} != {image_type}")
if ota_version != file_version:
    raise SystemExit(f"OTA file version mismatch: {ota_version} != {file_version}")
if total != ota_path.stat().st_size:
    raise SystemExit(
        f"OTA total_image_size mismatch: {total} != {ota_path.stat().st_size}"
    )

source_commit = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True
).strip()
source_dirty = bool(
    subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
)

manifest = {
    "schema": 1,
    "sourceCommit": source_commit,
    "sourceDirty": source_dirty,
    "board": board,
    "swBuildId": sw_build,
    "fileVersion": file_version,
    "manufacturerCode": manufacturer,
    "imageType": image_type,
    "nvmMigrationsVersion": nvm_schema,
    "canonicalConfig": canonical,
    "meter": {
        "type": "BL0937",
        "backend": "HLW8012-compatible pulse counter",
        "cf": "PA1",
        "cf1": "PC2",
        "sel": "PB1",
        "voltageMultiplier": voltage_multiplier,
        "currentMultiplier": current_multiplier,
        "powerMultiplier": power_multiplier,
        "protectionEnabled": True,
    },
    "legacyPmNvmMigration": {
        "sourceItems": {"energy": 40, "calibration": 44, "overload": 51},
        "destinationItems": {"energyEndpoint1": 64, "calibration": 68, "overload": 69},
        "copyOnly": True,
    },
    "artifacts": {},
    "otaHeader": {
        "headerVersion": hdr_version,
        "headerLength": hdr_len,
        "fieldControl": field_ctrl,
        "manufacturerCode": ota_mfr,
        "imageType": ota_type,
        "fileVersion": ota_version,
        "zigbeeStackVersion": stack_ver,
        "totalImageSize": total,
    },
    "note": "BUILD ONLY; no publication, device-config write, or device flash performed",
}

for path in (bin_path, ota_path):
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
