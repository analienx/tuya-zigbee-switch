#!/usr/bin/env bash
# Build one AVATTO or BSEED TS0726 board in the Analienx release line.
# Usage: make_scripts/build_analienx_board.sh BOARD [output-dir]
# BUILD ONLY: never publishes or flashes. Repository builds run on
# GitHub-hosted Actions only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: $0 BOARD [output-dir]" >&2; exit 2; }
REQUESTED="$1"
[[ "$REQUESTED" =~ ^[A-Z0-9_]+$ ]] || { echo 'ERROR: board must be a device_db.yaml name' >&2; exit 2; }
VARS="$(python3 helper_scripts/analienx_release.py vars --board "$REQUESTED")"
eval "$VARS"
[[ "$BOARD" == "$REQUESTED" ]] || { echo 'ERROR: release helper returned another board' >&2; exit 2; }

OUT_DIR="${2:-build/analienx-$BOARD}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"
BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"

COMMON_ARGS=(
  BSEED_LINK_POLICY=1
  BSEED_BUILD_DATE="$BUILD_DATE"
  VERSION_STR="$SW_BUILD"
  FILE_VERSION="$FILE_VERSION_HEX"
  NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
  DEVICE_TYPE=router
  CONFIG_STR="$CONFIG_STR"
  IMAGE_TYPE="$IMAGE_TYPE"
  MANUFACTURER_ID="$MANUFACTURER_CODE"
)

make -C src/telink clean
make -C src/telink build "${COMMON_ARGS[@]}" BIN_FILE="$BIN"
# Custom -> custom OTA on the board's own image type. No from_tuya wrapper:
# stock conversion is a separate, explicitly gated decision.
make -C src/telink ota "${COMMON_ARGS[@]}" BIN_FILE="$BIN" OTA_FILE="$OTA" \
  OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" OTA_IMAGE_TYPE="$IMAGE_TYPE" OTA_VERSION="$FILE_VERSION_HEX"

python3 helper_scripts/analienx_release.py verify --board "$BOARD" --ota "$OTA"

python3 - "$OUT_DIR" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" "$MANUFACTURER_CODE" "$IMAGE_TYPE" \
  "$NVM_SCHEMA" "$CONFIG_STR" "$BUILD_DATE" <<'PY'
import hashlib, json, pathlib, subprocess, sys
out = pathlib.Path(sys.argv[1])
board, build = sys.argv[2], sys.argv[3]
version, manufacturer, image_type, nvm = map(int, sys.argv[4:8])
config, date = sys.argv[8], sys.argv[9]
ota, native = out / 'forward.ota', out / 'forward.bin'
def digest(path, algorithm):
    return hashlib.new(algorithm, path.read_bytes()).hexdigest()
manifest = {
    'line': 'analienx', 'board': board, 'role': 'Router', 'swBuildId': build,
    'fileVersion': version, 'buildDate': date, 'nvmSchema': nvm, 'configStr': config,
    'linkPolicy': True,
    'otaHeader': {'manufacturerCode': manufacturer, 'imageType': image_type, 'fileVersion': version},
    'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'sourceDirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()),
    'artifacts': {
        'forward.ota': {'bytes': ota.stat().st_size, 'sha256': digest(ota, 'sha256'),
                        'sha512': digest(ota, 'sha512')},
        'forward.bin': {'bytes': native.stat().st_size, 'sha256': digest(native, 'sha256')},
    },
    'hardwareAcceptance': False,
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
