#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS0726-3-BS forward/recovery build.
#
# This deliberately bypasses board.mk's branch-depth-derived FILE_VERSION and
# Git-hash-derived VERSION_STR. The canary identity is a capability contract
# consumed by the target-only Z2M overlay, so both identifiers are fixed here.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BOARD="SWITCH_BSEED_TS0726_3GANG"
CANONICAL='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;'
SWAPPED='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=45577

FORWARD_SW_BUILD='1.1.4-bseedv4'
FORWARD_FILE_VERSION='0x11023003'   # decimal 285356035
RECOVERY_SW_BUILD='1.1.4-bseedv4r'
RECOVERY_FILE_VERSION='0x11023004'  # decimal 285356036

OUT_DIR="${1:-build/bseed-ts0726-v4}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

db_config="$(python3 - <<'PY'
import yaml
with open("device_db.yaml", "r", encoding="utf-8") as f:
    db = yaml.safe_load(f)
print(db["SWITCH_BSEED_TS0726_3GANG"]["config_str"])
PY
)"
if [[ "$db_config" != "$CANONICAL" ]]; then
    echo "ERROR: device_db canonical config drifted" >&2
    echo "expected: $CANONICAL" >&2
    echo "actual:   $db_config" >&2
    exit 2
fi

build_image() {
    local role="$1"
    local sw_build="$2"
    local file_version="$3"
    local revert="$4"
    local bin="$OUT_DIR/${role}.bin"
    local ota="$OUT_DIR/${role}.ota"

    make -C src/telink clean

    args=(
        VERSION_STR="$sw_build"
        FILE_VERSION="$file_version"
        DEVICE_TYPE=router
        CONFIG_STR="$CANONICAL"
        IMAGE_TYPE="$IMAGE_TYPE"
        MANUFACTURER_ID="$MANUFACTURER_CODE"
        BIN_FILE="$bin"
        MIGRATION_FROM_CONFIG="$SWAPPED"
    )
    if [[ "$revert" == "1" ]]; then
        args+=(MIGRATION_REVERT=1)
    fi

    make -C src/telink build "${args[@]}"

    ota_args=(
        VERSION_STR="$sw_build"
        FILE_VERSION="$file_version"
        DEVICE_TYPE=router
        CONFIG_STR="$CANONICAL"
        IMAGE_TYPE="$IMAGE_TYPE"
        MANUFACTURER_ID="$MANUFACTURER_CODE"
        BIN_FILE="$bin"
        OTA_FILE="$ota"
        OTA_MANUFACTURER_ID="$MANUFACTURER_CODE"
        OTA_IMAGE_TYPE="$IMAGE_TYPE"
        OTA_VERSION="$file_version"
        MIGRATION_FROM_CONFIG="$SWAPPED"
    )
    if [[ "$revert" == "1" ]]; then
        ota_args+=(MIGRATION_REVERT=1)
    fi

    make -C src/telink ota "${ota_args[@]}"
}

build_image forward "$FORWARD_SW_BUILD" "$FORWARD_FILE_VERSION" 0
build_image recovery "$RECOVERY_SW_BUILD" "$RECOVERY_FILE_VERSION" 1

python3 - "$OUT_DIR" <<'PY'
import hashlib
import json
import os
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
images = {
    "forward": {
        "swBuildId": "1.1.4-bseedv4",
        "fileVersion": 285356035,
        "manufacturerCode": 4417,
        "imageType": 45577,
    },
    "recovery": {
        "swBuildId": "1.1.4-bseedv4r",
        "fileVersion": 285356036,
        "manufacturerCode": 4417,
        "imageType": 45577,
    },
}

for role, meta in images.items():
    for suffix in ("bin", "ota"):
        path = out / f"{role}.{suffix}"
        data = path.read_bytes()
        meta[suffix] = {
            "fileName": path.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "sha512": hashlib.sha512(data).hexdigest(),
        }

manifest = {
    "schema": 1,
    "sourceCommit": os.environ.get("GITHUB_SHA") or "record git rev-parse HEAD at execution",
    "board": "SWITCH_BSEED_TS0726_3GANG",
    "canonicalConfig": "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;",
    "migrationFromConfig": "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;",
    "images": images,
}
try:
    import subprocess
    manifest["sourceCommit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
except Exception:
    pass

(out / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
