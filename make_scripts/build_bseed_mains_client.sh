#!/usr/bin/env bash
set -euo pipefail

# Reproducible experimental BSEED mains-client builds.
# BUILD ONLY: never publishes, flashes, changes a live device, or updates an OTA index.
#
# Usage:
#   build_bseed_mains_client.sh pm [output-dir]
#   build_bseed_mains_client.sh ts0726 [output-dir]

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TARGET="${1:-}"
case "$TARGET" in
pm)
    BOARD='OUTLET_BSEED_PM_TS011F'
    CANONICAL='b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'
    ROUTER_IMAGE_TYPE=43556
    CLIENT_IMAGE_TYPE=65024
    SW_BUILD='1.2.5-bseedcli2'
    FILE_VERSION_HEX='0x12053008'
    FILE_VERSION_DEC=302329864
    DEFAULT_OUT='build/bseed-ts011f-pm-client'
    EXTRA_ARGS=(
        BSEED_PM_B28WRPVX=1
        BSEED_PM_B28WRPVX_PROTECTION=1
        HLW8012_VOLTAGE_MULTIPLIER=161460
        HLW8012_CURRENT_MULTIPLIER=144679
        HLW8012_POWER_MULTIPLIER=16989
    )
    ;;
ts0726)
    BOARD='SWITCH_BSEED_TS0726_3GANG'
    CANONICAL='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;'
    SWAPPED='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;'
    ROUTER_IMAGE_TYPE=45577
    CLIENT_IMAGE_TYPE=65025
    SW_BUILD='1.1.8-bseedcli2'
    FILE_VERSION_HEX='0x1102300C'
    FILE_VERSION_DEC=285356044
    DEFAULT_OUT='build/bseed-ts0726-client'
    EXTRA_ARGS=(
        MIGRATION_FROM_CONFIG="$SWAPPED"
        DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG
    )
    ;;
*)
    echo "usage: $0 {pm|ts0726} [output-dir]" >&2
    exit 2
    ;;
esac

MANUFACTURER_CODE=4417
OUT_DIR="${2:-$DEFAULT_OUT}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"

python3 - "$BOARD" "$CANONICAL" "$ROUTER_IMAGE_TYPE" "$CLIENT_IMAGE_TYPE" <<'PY'
import sys
import yaml

board, canonical, router_image, client_image = sys.argv[1:]
router_image = int(router_image)
client_image = int(client_image)
with open("device_db.yaml", "r", encoding="utf-8") as f:
    db = yaml.safe_load(f)
entry = db[board]
assert entry["config_str"] == canonical, "canonical config drift"
assert int(entry["firmware_image_type"]) == router_image, "router image type drift"
assert int(entry["stock_manufacturer_id"]) == 4417, "manufacturer drift"
assert entry["device_type"] == "router", "validated production target is no longer router"
assert entry["mcu_family"] == "Telink", "target is no longer Telink"
assert entry["mcu"] == "TLSR8258", "target MCU drift"
used = {
    int(v["firmware_image_type"])
    for v in db.values()
    if isinstance(v, dict) and v.get("firmware_image_type") is not None
}
assert client_image not in used, f"experimental client image type {client_image} collides with device_db"
assert client_image != router_image
PY

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"
FROM_ROUTER_OTA="$OUT_DIR/from-router.ota"

COMMON_ARGS=(
    VERSION_STR="$SW_BUILD"
    FILE_VERSION="$FILE_VERSION_HEX"
    NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
    CONFIG_STR="$CANONICAL"
    IMAGE_TYPE="$CLIENT_IMAGE_TYPE"
    MANUFACTURER_ID="$MANUFACTURER_CODE"
    "${EXTRA_ARGS[@]}"
)

make -C src/telink -f client.mk clean
make -C src/telink -f client.mk build \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN"

# Native client -> client OTA identity. This is intentionally NOT added to any
# normal Zigbee2MQTT OTA index.
make -C src/telink -f client.mk ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$CLIENT_IMAGE_TYPE" \
    OTA_VERSION="$FILE_VERSION_HEX"

# Explicit already-custom router -> client transition wrapper. The compiled
# payload is identical to forward.ota after the OTA header; only the OUTER OTA
# image type matches the existing BSEED router. Never publish this in an index.
make -C src/telink -f client.mk ota \
    "${COMMON_ARGS[@]}" \
    BIN_FILE="$BIN" \
    OTA_FILE="$FROM_ROUTER_OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$ROUTER_IMAGE_TYPE" \
    OTA_VERSION="$FILE_VERSION_HEX"

python3 - "$OUT_DIR" "$TARGET" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" \
    "$MANUFACTURER_CODE" "$ROUTER_IMAGE_TYPE" "$CLIENT_IMAGE_TYPE" \
    "$NVM_SCHEMA" "$CANONICAL" <<'PY'
from __future__ import annotations

import hashlib
import json
import pathlib
import struct
import subprocess
import sys

(
    out_dir,
    target,
    board,
    sw_build,
    file_version,
    manufacturer,
    router_image_type,
    client_image_type,
    nvm_schema,
    canonical,
) = sys.argv[1:]
out = pathlib.Path(out_dir)
file_version = int(file_version)
manufacturer = int(manufacturer)
router_image_type = int(router_image_type)
client_image_type = int(client_image_type)
nvm_schema = int(nvm_schema)

paths = [out / "forward.bin", out / "forward.ota", out / "from-router.ota"]
for path in paths:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing/empty artifact: {path}")


def header(path: pathlib.Path) -> dict[str, int]:
    values = struct.unpack("<I5HIH32sI", path.read_bytes()[:56])
    magic, hv, hl, fc, mfr, image_type, version, stack, _, total = values
    if magic != 0x0BEEF11E or total != path.stat().st_size:
        raise SystemExit(f"invalid OTA header: {path.name}")
    return {
        "headerVersion": hv,
        "headerLength": hl,
        "fieldControl": fc,
        "manufacturerCode": mfr,
        "imageType": image_type,
        "fileVersion": version,
        "zigbeeStackVersion": stack,
        "totalImageSize": total,
    }

native = header(out / "forward.ota")
transition = header(out / "from-router.ota")
assert native["manufacturerCode"] == manufacturer
assert native["imageType"] == client_image_type
assert native["fileVersion"] == file_version
assert transition["manufacturerCode"] == manufacturer
assert transition["imageType"] == router_image_type
assert transition["fileVersion"] == file_version

native_bytes = (out / "forward.ota").read_bytes()
transition_bytes = (out / "from-router.ota").read_bytes()
assert native_bytes[56:] == transition_bytes[56:]
diffs = [i for i, (a, b) in enumerate(zip(native_bytes, transition_bytes)) if a != b]
expected_diffs = [
    12 + i
    for i, (a, b) in enumerate(
        zip(client_image_type.to_bytes(2, "little"), router_image_type.to_bytes(2, "little"))
    )
    if a != b
]
assert diffs == expected_diffs, (
    f"unexpected transition-wrapper header differences: {diffs}; "
    f"expected {expected_diffs}"
)

source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
manifest = {
    "schema": 2,
    "experimental": True,
    "sourceCommit": source_commit,
    "sourceDirty": source_dirty,
    "target": target,
    "board": board,
    "swBuildId": sw_build,
    "fileVersion": file_version,
    "manufacturerCode": manufacturer,
    "routerImageType": router_image_type,
    "clientImageType": client_image_type,
    "canonicalConfig": canonical,
    "nvmMigrationsVersion": nvm_schema,
    "role": {
        "logicalType": "end-device",
        "routing": False,
        "sleepy": False,
        "rxOnWhenIdle": True,
        "powerSource": "mains",
        "telinkLibrary": "libzb_ed",
        "pmEnabled": False,
        "pollControlCluster": False,
    },
    "binding": {
        "implementation": "shared switch_cluster.c router/client path",
        "roleSpecificBindingFork": False,
        "outputClustersRetained": ["OnOff", "LevelControl"],
        "defaultMode": "rise/press-start when no switch config is persisted",
        "persistedModeWins": True,
        "defaultDebounceMs": 20,
        "note": "client defaults avoid the upstream short/release binding plus 50ms debounce failure mode",
    },
    "roleTransition": {
        "storedDeviceTypeChanges": True,
        "zigbeeFactoryResetOnRouterToClient": True,
        "applicationNvClearCalled": False,
        "rejoinExpected": True,
        "permitJoinBeforeCanary": True,
    },
    "distribution": {
        "normalOtaIndex": False,
        "stockConversion": False,
        "fromRouterWrapper": "manual experimental transition only",
    },
    "artifacts": {},
    "otaHeader": native,
    "fromRouterOtaHeader": transition,
    "note": "BUILD ONLY; no publication or device flash performed",
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
