#!/usr/bin/env bash
set -euo pipefail

# Reproducible BSEED TS011F-BS non-PM Router build.
# BUILD ONLY: never publishes, flashes, or mutates a live device.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BOARD='OUTLET_BSEED_TS011F'
CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'
MANUFACTURER_CODE=4417
IMAGE_TYPE=43555
STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'
STOCK_IMAGE_TYPE=54179
SW_BUILD='1.1.3-bseedv8'
FILE_VERSION_HEX='0x11023001'
FILE_VERSION_DEC=285356033
DEFAULT_OUT='build/bseed-ts011f-nonpm-router'
# Opt-in next-version engineering candidate; the original Router v8 build
# remains reproducible by default and is never overwritten or published.
if [[ "${BSEED_ANTIBRICK_RC:-0}" == "1" || "${BSEED_ANTIBRICK_RC:-0}" == "2" ]]; then
  SW_BUILD='1.1.3-bseedv9'
  FILE_VERSION_HEX='0x11023012'
  FILE_VERSION_DEC=285356050
  DEFAULT_OUT='build/bseed-ts011f-nonpm-router-antibrick-rc'
  if [[ "${BSEED_ANTIBRICK_RC}" == "2" ]]; then
      SW_BUILD='1.1.3-bseedv10'
      FILE_VERSION_HEX='0x11023014'
      FILE_VERSION_DEC=285356052
      DEFAULT_OUT='build/bseed-ts011f-nonpm-router-antibrick-r2'
  fi
fi
OUT_DIR="${1:-$DEFAULT_OUT}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
NVM_SCHEMA="$(cat NVM_MIGRATIONS_VERSION 2>/dev/null || printf '1')"

readarray -t db_values < <(python3 - "$BOARD" <<'PY'
import sys, yaml
entry = yaml.safe_load(open('device_db.yaml', encoding='utf-8'))[sys.argv[1]]
for key in ('config_str','firmware_image_type','stock_manufacturer_id','stock_image_type','stock_manufacturer_name','device_type','mcu_family','mcu'):
    print(entry[key])
PY
)

[[ "${db_values[0]}" == "$CANONICAL" ]] || { echo 'ERROR: canonical config drift' >&2; exit 2; }
[[ "${db_values[1]}" == "$IMAGE_TYPE" ]] || { echo 'ERROR: custom image type drift' >&2; exit 2; }
[[ "${db_values[2]}" == "$MANUFACTURER_CODE" ]] || { echo 'ERROR: manufacturer drift' >&2; exit 2; }
[[ "${db_values[3]}" == "$STOCK_IMAGE_TYPE" ]] || { echo 'ERROR: stock image type drift' >&2; exit 2; }
[[ "${db_values[4]}" == "$STOCK_MANUFACTURER_NAME" ]] || { echo 'ERROR: stock manufacturer drift' >&2; exit 2; }
[[ "${db_values[5]}" == 'router' ]] || { echo 'ERROR: target is not a Router' >&2; exit 2; }
[[ "${db_values[6]}" == 'Telink' && "${db_values[7]}" == 'TLSR8258' ]] || { echo 'ERROR: MCU identity drift' >&2; exit 2; }

BIN="$OUT_DIR/forward.bin"
OTA="$OUT_DIR/forward.ota"
FROM_TUYA_OTA="$OUT_DIR/from_tuya.ota"
COMMON_ARGS=(
  VERSION_STR="$SW_BUILD"
  FILE_VERSION="$FILE_VERSION_HEX"
  NVM_MIGRATIONS_VERSION="$NVM_SCHEMA"
  DEVICE_TYPE=router
  DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM
  CONFIG_STR="$CANONICAL"
  IMAGE_TYPE="$IMAGE_TYPE"
  MANUFACTURER_ID="$MANUFACTURER_CODE"
)

make -C src/telink clean
make -C src/telink build "${COMMON_ARGS[@]}" BIN_FILE="$BIN"
make -C src/telink ota "${COMMON_ARGS[@]}" BIN_FILE="$BIN" OTA_FILE="$OTA" \
  OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" OTA_IMAGE_TYPE="$IMAGE_TYPE" OTA_VERSION="$FILE_VERSION_HEX"
make -C src/telink ota "${COMMON_ARGS[@]}" BIN_FILE="$BIN" OTA_FILE="$FROM_TUYA_OTA" \
  OTA_MANUFACTURER_ID="$MANUFACTURER_CODE" OTA_IMAGE_TYPE="$STOCK_IMAGE_TYPE" OTA_VERSION=0xFFFFFFFF

python3 - "$OUT_DIR" "$BOARD" "$SW_BUILD" "$FILE_VERSION_DEC" "$MANUFACTURER_CODE" "$IMAGE_TYPE" \
  "$STOCK_MANUFACTURER_NAME" "$STOCK_IMAGE_TYPE" "$NVM_SCHEMA" "$CANONICAL" <<'PY'
import hashlib, json, pathlib, struct, subprocess, sys
out = pathlib.Path(sys.argv[1])
board, sw_build = sys.argv[2], sys.argv[3]
file_version, manufacturer, image_type = map(int, sys.argv[4:7])
stock_name, stock_image_type = sys.argv[7], int(sys.argv[8])
nvm_schema, canonical = int(sys.argv[9]), sys.argv[10]
bin_path, ota_path, stock_path = out/'forward.bin', out/'forward.ota', out/'from_tuya.ota'
for p in (bin_path, ota_path, stock_path):
    assert p.is_file() and p.stat().st_size > 56, p

def header(path):
    data = path.read_bytes()
    return {
        'manufacturerCode': struct.unpack_from('<H', data, 10)[0],
        'imageType': struct.unpack_from('<H', data, 12)[0],
        'fileVersion': struct.unpack_from('<I', data, 14)[0],
        'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'sha512': hashlib.sha512(data).hexdigest(),
    }
normal, stock = header(ota_path), header(stock_path)
assert normal['manufacturerCode'] == manufacturer
assert normal['imageType'] == image_type
assert normal['fileVersion'] == file_version
assert stock['manufacturerCode'] == manufacturer
assert stock['imageType'] == stock_image_type
assert stock['fileVersion'] == 0xFFFFFFFF
normal_bytes = ota_path.read_bytes()
stock_bytes = stock_path.read_bytes()
if normal_bytes[56:] != stock_bytes[56:]:
    raise SystemExit('from-Tuya wrapper changed bytes after the 56-byte OTA header')
diff_offsets = [i for i, (a, b) in enumerate(zip(normal_bytes, stock_bytes)) if a != b]
expected_offsets = list(range(12, 18))
if diff_offsets != expected_offsets:
    raise SystemExit(f'unexpected from-Tuya wrapper diff offsets: {diff_offsets}; expected {expected_offsets}')
source_commit = subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()
source_dirty = bool(subprocess.check_output(['git','status','--porcelain'], text=True).strip())
manifest = {
    'board': board,
    'softwareBuild': sw_build,
    'fileVersion': file_version,
    'manufacturerCode': manufacturer,
    'imageType': image_type,
    'canonicalConfig': canonical,
    'nvmSchema': nvm_schema,
    'sourceCommit': source_commit,
    'sourceDirty': source_dirty,
    'forwardBinSha256': hashlib.sha256(bin_path.read_bytes()).hexdigest(),
    'forwardOta': normal,
    'otaHeader': normal,
    'fromTuyaOtaHeader': stock,
    'stockConversion': {
        'stockManufacturerName': stock_name,
        'stockImageType': stock_image_type,
        'outerVersion': 0xFFFFFFFF,
        'wrapperFileVersion': 0xFFFFFFFF,
        'headerDiffOffsets': diff_offsets,
        "payloadFromByte56Identical": True,
        'ota': stock,
    },
}
(out/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
