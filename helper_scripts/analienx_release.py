#!/usr/bin/env python3
"""Analienx firmware release line for AVATTO and BSEED TS0726 Router boards.

Analienx firmware (based on Romasku) carries this repository's shared Telink
Router hardening: Router recovery, the parent/route link policy and the
relay attributes the deployed converter reads. The upstream 1.1.2 builds that
these boards run today report OTA file version 0x11023000, so a plain
board.mk build of this tree would never be offered by Zigbee2MQTT. Every
board here gets the one release identity below, on its own image type.

Router boards only. Upstream builds the Router and End Device roles of a
board under the same image type and version, so a Router image offered to a
board running End Device firmware would silently change its role. Boards
whose device_db role is end_device (the no-neutral LZWSM16 modules, for
example) are left out unless the owner's live device is known to run the
Router build. An End Device line needs its own image types first, as the
BSEED Client lines have.

One identity, one set of bytes. zigbee2mqtt/ota/analienx_identity.json
records the sealed SHA-512 per board; once a build ID is sealed, a rebuild
with different bytes is refused and the release identity must move on.

Offline only: prints build variables, the CI matrix, and verifies produced
bytes. It never publishes or flashes firmware. Repository builds run on
GitHub-hosted Actions only.
"""
import argparse
import binascii
import datetime
import functools
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bseed_ota_identity import IdentityError, parse_ota_header  # noqa: E402
from bseed_pm_release import extract_native_image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEVICE_DB = ROOT / 'device_db.yaml'
OTA_DIR = ROOT / 'zigbee2mqtt' / 'ota'
INDEXES = (OTA_DIR / 'index_router.json', OTA_DIR / 'index_end_device.json')
REGISTRY = OTA_DIR / 'analienx_identity.json'

MANUFACTURER_CODE = 4417
# One identity for the whole set. The file version keeps the repository's
# 0x1102_30NN layout and stays above every version released for these image
# types (upstream 0x11023000). Change build, version and date together.
RELEASE = {'build': '1.1.2-anx1', 'version': 0x11023020, 'date': '20261112'}
ROLE = 'router'
# Whole OTA container limit of the Telink V3.7.2.0 512 KiB layout without a
# bootloader (src/telink/make_ota.py TELINK_OTA_MAX_BYTES).
OTA_MAX_BYTES = 0x34000

FAMILY_PREFIXES = ('MODULE_AVATTO_', 'SWITCH_AVATTO_', 'SWITCH_BSEED_TS0726_')
# Boards matching the prefixes that this release must not build, and why.
EXCLUDED = {
    'SWITCH_BSEED_TS0726_3GANG': 'has its own BSEED TS0726 line (make_scripts/build_bseed_ts0726_v8.sh)',
    'MODULE_AVATTO_DRY_TS0001': 'shares image type 43527 with MODULE_AVATTO_A_TS0001',
    'SWITCH_BSEED_TS0726_L_2GANG': 'shares image type 43633 with MODULE_NOUS_B1Z_TS0001',
    'SWITCH_BSEED_TS0726_L_3GANG_2': 'shares image type 47007 with MODULE_TUYA_COMMON_TS0001_2',
}
# End Device boards whose live unit runs the upstream Router build, so this
# Router release keeps their installed role. Evidence: Zigbee2MQTT database
# and state on 2026-11-12, BedroomDimmer is a Router on 0x11023000.
OWNER_ROUTER_BOARDS = ('SWITCH_BSEED_TS0726_1GANG',)
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


class ReleaseError(ValueError):
    pass


def load_db(path=DEVICE_DB):
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ReleaseError('device_db.yaml must be a mapping')
    return data


def role_of(name, entry):
    role = str(entry.get('device_type') or '').split('#')[0].strip()
    return 'router' if name in OWNER_ROUTER_BOARDS else role


def family(db):
    """Board name -> device_db entry for every board this release builds."""
    boards = {}
    for name, entry in db.items():
        if not name.startswith(FAMILY_PREFIXES) or name in EXCLUDED or not isinstance(entry, dict):
            continue
        if str(entry.get('build')).lower() not in ('yes', 'true'):
            continue
        if entry.get('mcu_family') != 'Telink' or not str(entry.get('mcu')).startswith('TLSR825'):
            continue
        if role_of(name, entry) != ROLE:
            continue
        boards[name] = entry
    return dict(sorted(boards.items()))


def end_device_boards(db):
    """Family boards left out because their canonical role is End Device."""
    return sorted(name for name, entry in db.items()
                  if name.startswith(FAMILY_PREFIXES) and name not in EXCLUDED and isinstance(entry, dict)
                  and str(entry.get('build')).lower() in ('yes', 'true') and role_of(name, entry) != ROLE)


@functools.lru_cache(maxsize=None)
def _released(indexes):
    versions = {}
    for path in indexes:
        if not Path(path).exists():
            continue
        for entry in json.loads(Path(path).read_text(encoding='utf-8')):
            if 'from_tuya' not in str(entry.get('fileName')):
                versions.setdefault(entry.get('imageType'), set()).add(entry.get('fileVersion'))
    return versions


def released_versions(image_type, indexes=INDEXES):
    """File versions already published for an image type in the committed OTA indexes."""
    return set(_released(tuple(str(p) for p in indexes)).get(image_type, ()))


def load_registry(path=REGISTRY):
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    sealed = data.get('sealed', {})
    if not isinstance(sealed, dict):
        raise ReleaseError('analienx_identity.json: sealed must be a mapping')
    return sealed


def validate(db, indexes=INDEXES, registry=None):
    build = RELEASE['build']
    if not build.isascii() or not 1 <= len(build) <= 16 or not BUILD_RE.fullmatch(build.encode()):
        raise ReleaseError('release build ID must be 1..16 ASCII bytes like 1.1.2-anx1')
    if RELEASE['version'] >> 8 != 0x110230:
        raise ReleaseError('release file version must keep the 0x110230NN layout')
    try:
        if datetime.datetime.strptime(RELEASE['date'], '%Y%m%d').strftime('%Y%m%d') != RELEASE['date']:
            raise ValueError
    except ValueError:
        raise ReleaseError('release date must be a real YYYYMMDD date') from None
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
    sealed = load_registry() if registry is None else registry
    unknown = sorted(set(sealed.get(build, {})) - set(boards))
    if unknown:
        raise ReleaseError(f'sealed {build} boards not in the release: {unknown}')
    for seals in sealed.values():
        for board, digest in seals.items():
            if not re.fullmatch(r'[0-9a-f]{128}', str(digest)):
                raise ReleaseError(f'sealed {board}: not a SHA-512')
    return boards


def candidate(db, board, registry=None):
    sealed = load_registry() if registry is None else registry
    boards = validate(db, registry=sealed)
    if board not in boards:
        if board in EXCLUDED:
            reason = EXCLUDED[board]
        elif board in end_device_boards(db):
            reason = 'its device_db role is end_device; this release is Router-only'
        else:
            reason = 'not an AVATTO or BSEED TS0726 Telink board with build: yes'
        raise ReleaseError(f'{board} is not in this release: {reason}')
    return {'board': board, 'build': RELEASE['build'], 'version': RELEASE['version'],
            'date': RELEASE['date'], 'type': int(boards[board]['firmware_image_type']),
            'manufacturer': MANUFACTURER_CODE, 'configStr': boards[board]['config_str'],
            'live': board in LIVE, 'sealedSha512': sealed.get(RELEASE['build'], {}).get(board)}


def build_ids(firmware):
    """Length-prefixed release build IDs, as the Basic cluster stores swBuildId."""
    found = set()
    for match in re.finditer(rb'1\.\d+\.\d+-anx', firmware):
        start = match.start()
        if start == 0:
            continue
        length = firmware[start - 1]
        text = firmware[start:start + length]
        if len(text) == length and BUILD_RE.fullmatch(text):
            found.add(text.decode('ascii'))
    return found


def verify_native_image(data, expected):
    """Check the produced OTA bytes against the release identity, not the manifest."""
    try:
        header = parse_ota_header(data)
        if header['header_length'] != 56 or (struct.unpack_from('<H', data, 4)[0],
                                             struct.unpack_from('<H', data, 8)[0],
                                             struct.unpack_from('<H', data, 18)[0]) != (0x100, 0, 2):
            raise ReleaseError('unsupported OTA header format')
        if len(data) > OTA_MAX_BYTES:
            raise ReleaseError(f'OTA container is {len(data)} bytes; the SDK accepts at most {OTA_MAX_BYTES}')
        if (header['manufacturer_code'], header['image_type'], header['file_version']) != (
                expected['manufacturer'], expected['type'], expected['version']):
            raise ReleaseError('wrong transport identity %d/%d/%#x' % (
                header['manufacturer_code'], header['image_type'], header['file_version']))
        firmware = extract_native_image(data, header)
    except IdentityError as error:
        raise ReleaseError(str(error)) from error
    if firmware[6:12] != b'\x5d\x02KNLT':
        raise ReleaseError('bad startup marker')
    if struct.unpack_from('<I', firmware, 2)[0] != expected['version']:
        raise ReleaseError('embedded version differs from the OTA version')
    if struct.unpack_from('<I', firmware, 24)[0] != len(firmware):
        raise ReleaseError('firmware length field mismatch')
    if struct.unpack_from('<I', firmware, len(firmware) - 4)[0] != (binascii.crc32(firmware[:-4]) ^ 0xFFFFFFFF):
        raise ReleaseError('firmware CRC mismatch')
    ids = build_ids(firmware)
    if expected['build'] not in ids:
        raise ReleaseError('missing length-prefixed Basic build ID')
    if ids - {expected['build']}:
        raise ReleaseError(f'stray release build IDs in the image: {sorted(ids - {expected["build"]})}')
    for offset in (0x8008, 0x20008):
        if firmware[offset:offset + 4] == b'KNLT':
            raise ReleaseError(f'second start marker at {offset:#x}')
    sealed = expected.get('sealedSha512')
    if sealed and hashlib.sha512(data).hexdigest() != sealed:
        raise ReleaseError(f'{expected["board"]}: {expected["build"]} is sealed with other bytes; '
                           'move RELEASE to a new build ID and version for changed firmware')
    return {'nativeIntegrityVerified': True, 'basicBuildIdVerified': True,
            'sealed': bool(sealed), 'hardwareAcceptance': False}


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
            sealed = load_registry().get(RELEASE['build'], {})
            print(f'Release {RELEASE["build"]} at {RELEASE["version"]:#010x}, role {ROLE}')
            for name, entry in boards.items():
                print(f'  {"LIVE " if name in LIVE else "     "}{name:32} type {entry["firmware_image_type"]}'
                      f'{"  sealed" if name in sealed else ""}')
            for name, reason in EXCLUDED.items():
                print(f'  excluded {name}: {reason}')
            for name in end_device_boards(db):
                print(f'  excluded {name}: device_db role is end_device')
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
