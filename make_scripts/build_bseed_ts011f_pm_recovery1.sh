#!/usr/bin/env bash
set -euo pipefail

# V8-lineage recovery for the already-custom BSEED TS011F-BS-PM canary.
# BUILD ONLY. This script never publishes, flashes, writes device config, or
# performs any live-device action.
#
# Recovery design:
#   * retain the accepted V8 Telink SDK/HAL/OTA/Zigbee platform lineage;
#   * retain the hardware-proven b28wrpvx pins/calibration/protection identity;
#   * restore 8b8cc492 nonzero-pulse PM semantics by disabling V8's later
#     low-load suppression in hlw8012.h;
#   * advance both inner and Zigbee OTA version to 0x12053003 (> V8 0x12053002).

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
V8_BASE_COMMIT='ded91a1fb1cdeb320d0858c8f4bcabab32bf5564'
KNOWN_GOOD_PM_COMMIT='8b8cc4924a353b35880666f7b48f0afbee89eb17'

OUT_DIR="${1:-build/bseed-ts011f-pm-recovery1}"
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
[[ "${db_values[3]}" == "router" ]] || { echo "ERROR: device type drift" >&2; exit 2; }
[[ "${db_values[4]}" == "Telink" ]] || { echo "ERROR: MCU family drift" >&2; exit 2; }
[[ "${db_values[5]}" == "TLSR8258" ]] || { echo "ERROR: MCU drift" >&2; exit 2; }

grep -Fq '#define HLW8012_NO_LOAD_POWER_W              (-1)' \
  src/base_components/energy_measurement/hlw8012.h || {
    echo "ERROR: recovery PM semantics are not enabled" >&2
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
make -C src/telink build "${COMMON_ARGS[@]}" BIN_FILE="$BIN"
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
    "$V8_BASE_COMMIT" "$KNOWN_GOOD_PM_COMMIT" <<'PY'
from __future__ import annotations

import binascii
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
    v8_base_commit,
    known_good_pm_commit,
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

bin_data = bin_path.read_bytes()
ota_data = ota_path.read_bytes()
if len(bin_data) < 8:
    raise SystemExit("firmware binary too short")
inner_version = int.from_bytes(bin_data[2:6], "little")
if inner_version != file_version:
    raise SystemExit(
        f"inner Telink fileVersion mismatch: {inner_version} != {file_version}"
    )

# Zigbee OTA header: <I5HIH32sI (56 bytes), then subelement <HI (6 bytes).
header = struct.unpack("<I5HIH32sI", ota_data[:56])
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
if ota_mfr != manufacturer or ota_type != image_type or ota_version != file_version:
    raise SystemExit("OTA identity/version mismatch")
if total != len(ota_data):
    raise SystemExit(f"OTA total_image_size mismatch: {total} != {len(ota_data)}")
sub_id, sub_len = struct.unpack("<HI", ota_data[56:62])
if sub_id != 0 or sub_len != len(ota_data) - 62:
    raise SystemExit("OTA subelement mismatch")
payload = ota_data[62:]
if int.from_bytes(payload[2:6], "little") != file_version:
    raise SystemExit("OTA payload inner version mismatch")
if payload[6:8] != b"\x5d\x02":
    raise SystemExit("missing Telink OTA payload magic")
if len(payload) < 12:
    raise SystemExit("OTA payload too short")
expected_crc = binascii.crc32(payload[:-4]) ^ 0xFFFFFFFF
actual_crc = int.from_bytes(payload[-4:], "little")
if actual_crc != expected_crc:
    raise SystemExit(
        f"Telink payload CRC mismatch: 0x{actual_crc:08x} != 0x{expected_crc:08x}"
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
    "innerFileVersion": inner_version,
    "manufacturerCode": manufacturer,
    "imageType": image_type,
    "nvmMigrationsVersion": nvm_schema,
    "canonicalConfig": canonical,
    "v8PlatformBaseCommit": v8_base_commit,
    "knownGoodPmBehaviorCommit": known_good_pm_commit,
    "recoveryContract": {
        "otaPlatformLineage": "V8/Telink current lineage",
        "pmBehavior": "8b8cc492 nonzero CF pulse/energy semantics",
        "noLoadSuppression": False,
        "liveMutationAuthorized": False,
    },
    "meter": {
        "type": "BL0937",
        "backend": "HLW8012-compatible Telink hardware pulse counters",
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
        "subElementLength": sub_len,
        "telinkPayloadCrc32Xor": f"0x{actual_crc:08x}",
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
