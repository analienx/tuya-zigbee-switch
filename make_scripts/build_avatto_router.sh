#!/usr/bin/env bash
# Build one AVATTO ZWSM16 Router image with its explicit release identity.
# Usage: make_scripts/build_avatto_router.sh {ts0001|ts0002} [output-dir]
# Repository builds run on GitHub-hosted Actions only.
set -euo pipefail

USAGE="usage: $0 ts0001|ts0002 [output-dir]"
[[ $# -ge 1 ]] || { echo "$USAGE" >&2; exit 2; }
VARIANT="$1"
eval "$(python3 helper_scripts/avatto_release.py vars --variant "$VARIANT")"

OUT_DIR="${2:-build/avatto-$VARIANT-router}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"

readarray -t db_values < <(python3 - "$BOARD" <<'PY'
import sys, yaml
entry = yaml.safe_load(open('device_db.yaml', encoding='utf-8'))[sys.argv[1]]
for key in ('config_str', 'firmware_image_type', 'stock_manufacturer_id',
            'device_type', 'mcu_family', 'mcu', 'build'):
    print(entry[key])
PY
)
CONFIG_STR="${db_values[0]}"
[[ "${db_values[1]}" == "$IMAGE_TYPE" ]] || { echo 'ERROR: image type drift' >&2; exit 2; }
[[ "${db_values[2]}" == "$MANUFACTURER_CODE" ]] || { echo 'ERROR: manufacturer drift' >&2; exit 2; }
[[ "${db_values[3]}" == 'router' ]] || { echo 'ERROR: target is not a Router' >&2; exit 2; }
[[ "${db_values[4]}" == 'Telink' && "${db_values[5]}" == 'TLSR8258' ]] || {
    echo 'ERROR: MCU identity drift' >&2; exit 2; }

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"
COMMON_ARGS=(
  VERSION_STR="$SW_BUILD"
  FILE_VERSION="$FILE_VERSION_HEX"
  NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
  DEVICE_TYPE=router
  CONFIG_STR="$CONFIG_STR"
  IMAGE_TYPE="$IMAGE_TYPE"
  MANUFACTURER_ID="$MANUFACTURER_CODE"
  BSEED_LINK_POLICY=1
)

make -C src/telink clean
make -C src/telink build "${COMMON_ARGS[@]}" BIN_FILE="$BIN"
make -C src/telink ota "${COMMON_ARGS[@]}" BIN_FILE="$BIN" OTA_FILE="$OTA" \
  OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" OTA_IMAGE_TYPE="$IMAGE_TYPE" OTA_VERSION="$FILE_VERSION_HEX"

python3 helper_scripts/avatto_release.py verify --variant "$VARIANT" --ota "$OTA"

python3 - "$OUT_DIR" "$VARIANT" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" \
  "$MANUFACTURER_CODE" "$IMAGE_TYPE" "$NVM_SCHEMA" "$CONFIG_STR" "$BUILD_DATE" <<'PY'
import hashlib, json, pathlib, subprocess, sys
out = pathlib.Path(sys.argv[1])
variant, board, build = sys.argv[2], sys.argv[3], sys.argv[4]
version, manufacturer, image_type, nvm = map(int, sys.argv[5:9])
config, date = sys.argv[9], sys.argv[10]
def digest(p, algo):
    return hashlib.new(algo, p.read_bytes()).hexdigest()
ota, binf = out / 'forward.ota', out / 'forward.bin'
manifest = {
    'variant': 'avatto-' + variant, 'board': board, 'role': 'Router',
    'build': build, 'fileVersion': version, 'manufacturerCode': manufacturer,
    'imageType': image_type, 'nvmSchema': nvm, 'configStr': config,
    'buildDate': date, 'linkPolicy': True,
    'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'sourceDirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()),
    'otaBytes': ota.stat().st_size, 'otaSha256': digest(ota, 'sha256'),
    'otaSha512': digest(ota, 'sha512'), 'nativeSha256': digest(binf, 'sha256'),
    'hardwareAcceptance': False,
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
