#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS0726-3-BS v6 forward build.
# The emergency rollback is deliberately built from the frozen v5 source tree
# at a higher OTA fileVersion; this script therefore produces forward only.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CANONICAL='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;'
SWAPPED='iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=45577
FORWARD_SW_BUILD='1.1.6-bseedv6'
FORWARD_FILE_VERSION='0x11023007'   # decimal 285356039

OUT_DIR="${1:-build/bseed-ts0726-v6}"
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

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"

make -C src/telink clean
make -C src/telink build \
    VERSION_STR="$FORWARD_SW_BUILD" \
    FILE_VERSION="$FORWARD_FILE_VERSION" \
    DEVICE_TYPE=router \
    CONFIG_STR="$CANONICAL" \
    IMAGE_TYPE="$IMAGE_TYPE" \
    MANUFACTURER_ID="$MANUFACTURER_CODE" \
    BIN_FILE="$BIN" \
    MIGRATION_FROM_CONFIG="$SWAPPED" \
    DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG

make -C src/telink ota \
    VERSION_STR="$FORWARD_SW_BUILD" \
    FILE_VERSION="$FORWARD_FILE_VERSION" \
    DEVICE_TYPE=router \
    CONFIG_STR="$CANONICAL" \
    IMAGE_TYPE="$IMAGE_TYPE" \
    MANUFACTURER_ID="$MANUFACTURER_CODE" \
    BIN_FILE="$BIN" \
    OTA_FILE="$OTA" \
    OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" \
    OTA_IMAGE_TYPE="$IMAGE_TYPE" \
    OTA_VERSION="$FORWARD_FILE_VERSION" \
    MIGRATION_FROM_CONFIG="$SWAPPED" \
    DEVICE_CONFIG_GUARD=BSEED_TS0726_3GANG

python3 - "$OUT_DIR" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

out = pathlib.Path(sys.argv[1])
meta = {
    "swBuildId": "1.1.6-bseedv6",
    "fileVersion": 285356039,
    "manufacturerCode": 4417,
    "imageType": 45577,
}
for suffix in ("bin", "ota"):
    path = out / f"forward.{suffix}"
    data = path.read_bytes()
    meta[suffix] = {
        "fileName": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
    }

manifest = {
    "schema": 1,
    "sourceCommit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "board": "SWITCH_BSEED_TS0726_3GANG",
    "canonicalConfig": "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;",
    "migrationFromConfig": "iedhxgyi;TS0726-3-BS;LC4;SB1u;RC0;IC2;SB7u;RD7;IC3;SB4u;RD2;IB5;M;",
    "images": {"forward": meta},
}
(out / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
