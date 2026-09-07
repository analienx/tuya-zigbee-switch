#!/usr/bin/env bash
set -euo pipefail

# BUILD ONLY. Produces the V8-lineage recovery image for the already-custom
# BSEED TS011F-BS-PM canary. It never publishes, flashes, or writes device NVM.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BOARD='OUTLET_BSEED_PM_TS011F'
CANONICAL='b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=43556
SW_BUILD='1.2.5-bseed-pm-recovery1'
FILE_VERSION_HEX='0x12053003'
FILE_VERSION_DEC=302329859
VOLTAGE_MULTIPLIER=161460
CURRENT_MULTIPLIER=144679
POWER_MULTIPLIER=16989
V8_ACCEPTED_BASE_SHA='ded91a1fb1cdeb320d0858c8f4bcabab32bf5564'
V8_ACCEPTED_OTA_SHA256='c3ccb484c28d7ef08594acc306b2054aed3ba9fcfc9579643da339f7fcc9fe7c'

OUT_DIR="${1:-build/bseed-ts011f-pm-recovery-v12053003}"
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

[[ "${db_values[0]}" == "$CANONICAL" ]] || { echo "ERROR: canonical config drift" >&2; exit 2; }
[[ "${db_values[1]}" == "$IMAGE_TYPE" ]] || { echo "ERROR: image type drift" >&2; exit 2; }
[[ "${db_values[2]}" == "$MANUFACTURER_CODE" ]] || { echo "ERROR: manufacturer drift" >&2; exit 2; }
[[ "${db_values[3]}" == "router" ]] || { echo "ERROR: target is not router" >&2; exit 2; }
[[ "${db_values[4]}" == "Telink" ]] || { echo "ERROR: target is not Telink" >&2; exit 2; }
[[ "${db_values[5]}" == "TLSR8258" ]] || { echo "ERROR: MCU drift" >&2; exit 2; }

grep -q 'FILE_VERSION == 0x12053003' src/base_components/energy_measurement/hlw8012.c || {
    echo "ERROR: recovery semantics are not coupled to 0x12053003" >&2
    exit 2
}

grep -q 'BSEED_PM_RECOVERY_PREDECESSOR_SEMANTICS' src/base_components/energy_measurement/hlw8012.c || {
    echo "ERROR: recovery PM semantics gate missing" >&2
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
    "$VOLTAGE_MULTIPLIER" "$CURRENT_MULTIPLIER" "$POWER_MULTIPLIER" \
    "$V8_ACCEPTED_BASE_SHA" "$V8_ACCEPTED_OTA_SHA256" <<'PY'
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
    accepted_base_sha,
    accepted_ota_sha,
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

raw = ota_path.read_bytes()
header = struct.unpack("<I5HIH32sI", raw[:56])
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
if (ota_mfr, ota_type, ota_version) != (manufacturer, image_type, file_version):
    raise SystemExit(
        f"OTA identity mismatch: {(ota_mfr, ota_type, ota_version)} != "
        f"{(manufacturer, image_type, file_version)}"
    )
if total != len(raw):
    raise SystemExit(f"OTA size mismatch: {total} != {len(raw)}")

source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())

manifest = {
    "schema": 1,
    "purpose": "V8-lineage recovery with hardware-proven predecessor PM semantics",
    "sourceCommit": source_commit,
    "sourceDirty": source_dirty,
    "acceptedV8BaseSha": accepted_base_sha,
    "acceptedV8OtaSha256": accepted_ota_sha,
    "board": board,
    "swBuildId": sw_build,
    "fileVersion": file_version,
    "manufacturerCode": manufacturer,
    "imageType": image_type,
    "nvmMigrationsVersion": nvm_schema,
    "canonicalConfig": canonical,
    "recoverySemantics": {
        "activation": "BSEED_PM_B28WRPVX && FILE_VERSION == 0x12053003",
        "selStartup": "8b8cc492 hal_gpio_init(output-low) then hal_gpio_set(high)",
        "lowLoadSuppression": False,
        "energyAccumulation": "every sane CF sample",
        "v8PlatformRetained": True,
        "v8NvmMigrationRetained": True,
        "v8OtaStackRetained": True,
    },
    "meter": {
        "type": "BL0937",
        "backend": "HLW8012-compatible Telink hardware pulse counter",
        "cf": "PA1",
        "cf1": "PC2",
        "sel": "PB1",
        "voltageMultiplier": voltage_multiplier,
        "currentMultiplier": current_multiplier,
        "powerMultiplier": power_multiplier,
        "protectionEnabled": True,
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
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
