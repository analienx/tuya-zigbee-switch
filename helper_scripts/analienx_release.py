#!/usr/bin/env python3
"""Analienx firmware release line for AVATTO and BSEED TS0726 boards.

Analienx firmware (based on Romasku) carries this repository's shared Telink
Router hardening: Router recovery, the parent/route link policy and the
relay attributes the deployed converter reads. The upstream 1.1.2 builds that
these boards run today report OTA file version 0x11023000, so a plain
board.mk build of this tree would never be offered by Zigbee2MQTT. Every
board here gets the one release identity below, on its own image type.

One role per board. Upstream builds the Router and End Device roles of a
board under the same image type and version, so a release can only carry one
of them without new image types. All live boards are Routers, so this line
is Router-only; an End Device line needs its own image types first, as the
BSEED Client lines have.

Offline only: prints build variables, the CI matrix, and verifies produced
bytes. It never publishes or flashes firmware. Repository builds run on
GitHub-hosted Actions only.
"""
import argparse
import binascii
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_DB = ROOT / 'device_db.yaml'
INDEXES = (ROOT / 'zigbee2mqtt' / 'ota' / 'index_router.json',
           ROOT / 'zigbee2mqtt' / 'ota' / 'index_end_device.json')

MANUFACTURER_CODE = 4417
# One identity for the whole set. The file version keeps the repository's
# 0x1102_30NN layout and stays above every version released for these image
# types (upstream 0x11023000). Change build, version and date together.
RELEASE = {'build': '1.1.2-anx1', 'version': 0x11023020, 'date': '20261112'}
ROLE = 'router'

FAMILY_PREFIXES = ('MODULE_AVATTO_', 'SWITCH_AVATTO_', 'SWITCH_BSEED_TS0726_')
# Boards matching the prefixes that this release must not build, and why.
EXCLUDED = {
    'SWITCH_BSEED_TS0726_3GANG': 'has its own BSEED TS0726 line (make_scripts/build_bseed_ts0726_v8.sh)',
    'MODULE_AVATTO_DRY_TS0001': 'shares image type 43527 with MODULE_AVATTO_A_TS0001',
    'SWITCH_BSEED_TS0726_L_2GANG': 'shares image type 43633 with MODULE_NOUS_B1Z_TS0001',
    'SWITCH_BSEED_TS0726_L_3GANG_2': 'shares image type 47007 with MODULE_TUYA_COMMON_TS0001_2',
}
# Upstream image-type collisions a release board must live with. A device
# reports its installed image type in every OTA query, so the live board
# cannot move to a new type without an image-type migration. The other board
# is never built by this release, and any OTA index entry for the release
# board must filter by its Zigbee manufacturer name.
ACCEPTED_SHARED_TYPES = {
    'MODULE_AVATTO_A_TS0001': ('MODULE_AVATTO_DRY_TS0001',),
}
# Boards running in the owner's Home Assistant on 2026-11-12, all as Routers.
LIVE = ('MODULE_AVATTO_A_TS0001', 'MODULE_AVATTO_A_TS0002', 'SWITCH_BSEED_TS0726_1GANG')

BUILD_RE = re.compile(rb'1\.\d+\.\d+-anx[0-9a-z]{1,8}')
OTA_MAGIC = 0x0BEEF11E
SLOT_BYTES = 0x34000


class ReleaseError(ValueError):
    pass


def load_db(path=DEVICE_DB):
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ReleaseError('device_db.yaml must be a mapping')
    return data


def family(db):
    """Board name -> device_db entry for every board this release builds."""
    boards = {}
    for name, entry in db.items():
        if not name.startswith(FAMILY_PREFIXES) or name in EXCLUDED:
            continue
        if not isinstance(entry, dict) or str(entry.get('build')).lower() not in ('yes', 'true'):
            continue
        if entry.get('mcu_family') != 'Telink' or not str(entry.get('mcu')).startswith('TLSR825'):
            continue
        boards[name] = entry
    return dict(sorted(boards.items()))


def released_versions(image_type, indexes=INDEXES):
    """File versions already published for an image type in the committed OTA indexes."""
    versions = set()
    for path in indexes:
        if not Path(path).exists():
            continue
        for entry in json.loads(Path(path).read_text(encoding='utf-8')):
            if entry.get('imageType') == image_type and 'from_tuya' not in str(entry.get('fileName')):
                versions.add(entry.get('fileVersion'))
    return versions


def validate(db, indexes=INDEXES):
    build = RELEASE['build']
    if not build.isascii() or not 1 <= len(build) <= 16 or not BUILD_RE.fullmatch(build.encode()):
        raise ReleaseError('release build ID must be 1..16 ASCII bytes like 1.1.2-anx1')
    if RELEASE['version'] >> 8 != 0x110230:
        raise ReleaseError('release file version must keep the 0x110230NN layout')
    if not re.fullmatch(r'20\d{6}', RELEASE['date']):
        raise ReleaseError('release date must be YYYYMMDD')
    boards = family(db)
    for name in LIVE:
        if name not in boards:
            raise ReleaseError(f'live board {name} is missing from the release')
    owners = {}
    for name, entry in db.items():
        if isinstance(entry, dict) and entry.get('firmware_image_type') is not None:
            owners.setdefault(int(entry['firmware_image_type']), []).append(name)
    for name, entry in boards.items():
        image_type = int(entry['firmware_image_type'])
        others = sorted(o for o in owners[image_type] if o != name)
        unexpected = sorted(set(others) - set(ACCEPTED_SHARED_TYPES.get(name, ())))
        if unexpected:
            raise ReleaseError(f'{name}: image type {image_type} is shared with {", ".join(unexpected)}')
        if any(other in boards for other in others):
            raise ReleaseError(f'{name}: two release boards share image type {image_type}')
        if int(entry['stock_manufacturer_id']) != MANUFACTURER_CODE:
            raise ReleaseError(f'{name}: manufacturer code is not {MANUFACTURER_CODE}')
        newer = [v for v in released_versions(image_type, indexes) if v >= RELEASE['version']]
        if newer:
            raise ReleaseError(f'{name}: version {RELEASE["version"]:#010x} is not above released {newer}')
    return boards


def candidate(db, board):
    boards = validate(db)
    if board not in boards:
        reason = EXCLUDED.get(board, 'not an AVATTO or BSEED TS0726 Telink board with build: yes')
        raise ReleaseError(f'{board} is not in this release: {reason}')
    entry = boards[board]
    return {'board': board, 'role': ROLE, 'build': RELEASE['build'], 'version': RELEASE['version'],
            'date': RELEASE['date'], 'type': int(entry['firmware_image_type']),
            'manufacturer': MANUFACTURER_CODE, 'configStr': entry['config_str'],
            'zigbeeModel': str(entry['config_str']).split(';')[1],
            'stockManufacturerName': entry.get('stock_manufacturer_name'), 'live': board in LIVE}


def verify_native_image(data, expected):
    """Check the produced OTA bytes against the release identity, not the manifest."""
    if len(data) < 62 or struct.unpack_from('<I', data, 0)[0] != OTA_MAGIC:
        raise ReleaseError('not a Zigbee OTA file')
    version, header_length, control = struct.unpack_from('<HHH', data, 4)
    manufacturer, image_type, file_version = struct.unpack_from('<HHI', data, 10)
    if (version, header_length, control, struct.unpack_from('<H', data, 18)[0]) != (0x100, 56, 0, 2):
        raise ReleaseError('unsupported OTA header format')
    if struct.unpack_from('<I', data, 52)[0] != len(data):
        raise ReleaseError('OTA total size does not match the file')
    if (manufacturer, image_type, file_version) != (expected['manufacturer'], expected['type'],
                                                    expected['version']):
        raise ReleaseError(f'wrong transport identity {manufacturer}/{image_type}/{file_version:#x}')
    tag, size = struct.unpack_from('<HI', data, 56)
    firmware = data[62:]
    if tag != 0 or size != len(firmware):
        raise ReleaseError('expected exactly one firmware sub-element')
    if len(firmware) >= SLOT_BYTES:
        raise ReleaseError(f'image is {len(firmware)} bytes; a slot holds {SLOT_BYTES - 1}')
    if firmware[6:12] != b'\x5d\x02KNLT':
        raise ReleaseError('bad startup marker')
    if struct.unpack_from('<I', firmware, 2)[0] != expected['version']:
        raise ReleaseError('embedded version differs from the OTA version')
    if struct.unpack_from('<I', firmware, 24)[0] != len(firmware):
        raise ReleaseError('firmware length field mismatch')
    if struct.unpack_from('<I', firmware, len(firmware) - 4)[0] != (binascii.crc32(firmware[:-4]) ^ 0xFFFFFFFF):
        raise ReleaseError('firmware CRC mismatch')
    build = expected['build'].encode('ascii')
    if bytes([len(build)]) + build not in firmware:
        raise ReleaseError('missing length-prefixed Basic build ID')
    strays = sorted({m.decode() for m in BUILD_RE.findall(firmware)} - {expected['build']})
    if strays:
        raise ReleaseError(f'stray release build IDs in the image: {strays}')
    for offset in (0x8008, 0x20008):
        if firmware[offset:offset + 4] == b'KNLT':
            raise ReleaseError(f'second start marker at {offset:#x}')
    return {'nativeIntegrityVerified': True, 'basicBuildIdVerified': True, 'hardwareAcceptance': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('matrix', help='GitHub Actions matrix JSON of every board in the release')
    sub.add_parser('list', help='human-readable list of included and excluded boards')
    variables = sub.add_parser('vars', help='shell assignments for one board')
    variables.add_argument('--board', required=True)
    check = sub.add_parser('verify', help='verify a produced forward.ota')
    check.add_argument('--board', required=True)
    check.add_argument('--ota', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        db = load_db()
        if args.cmd == 'matrix':
            boards = validate(db)
            print(json.dumps({'include': [{'board': b, 'live': b in LIVE} for b in boards]}))
        elif args.cmd == 'list':
            boards = validate(db)
            print(f'Release {RELEASE["build"]} at {RELEASE["version"]:#010x}, role {ROLE}')
            for name, entry in boards.items():
                print(f'  {"LIVE " if name in LIVE else "     "}{name:32} type {entry["firmware_image_type"]}')
            for name, reason in EXCLUDED.items():
                print(f'  excluded {name}: {reason}')
        elif args.cmd == 'vars':
            c = candidate(db, args.board)
            for key, value in (('BOARD', c['board']), ('SW_BUILD', c['build']),
                               ('FILE_VERSION_HEX', f'0x{c["version"]:08X}'),
                               ('FILE_VERSION_DEC', str(c['version'])), ('IMAGE_TYPE', str(c['type'])),
                               ('MANUFACTURER_CODE', str(c['manufacturer'])),
                               ('BUILD_DATE', c['date']), ('CONFIG_STR', c['configStr'])):
                if "'" in value:
                    raise ReleaseError(f'{key} contains a quote')
                print(f"{key}='{value}'")
        else:
            c = candidate(db, args.board)
            verify_native_image(args.ota.read_bytes(), c)
            print(f'{c["board"]}: native image verified, {c["build"]} {c["version"]:#010x} type {c["type"]}')
    except ReleaseError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
