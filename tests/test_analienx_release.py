"""Offline checks of the Analienx AVATTO / BSEED TS0726 release set and verifier.

Real builds, TC32 starts and OTA self-activation run in
.github/workflows/analienx-release.yml on GitHub-hosted Actions.
"""
import binascii
import copy
import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import analienx_release as release  # noqa: E402
from analienx_release import ReleaseError  # noqa: E402

DB = release.load_db()


def test_release_set_covers_live_boards_and_explains_every_exclusion():
    boards = release.validate(DB)
    for board in release.LIVE:
        assert board in boards
    assert 'SWITCH_BSEED_TS0726_3GANG' not in boards, 'the 3-gang keeps its own BSEED line'
    for board, reason in release.EXCLUDED.items():
        assert board in DB, board
        assert board not in boards and reason
    expected = {name for name, entry in DB.items()
                if name.startswith(release.FAMILY_PREFIXES) and name not in release.EXCLUDED
                and str(entry.get('build')).lower() in ('yes', 'true')}
    assert set(boards) == expected, 'every buildable AVATTO and TS0726 board is in the release'
    assert len(boards) >= 18


def test_each_board_owns_its_image_type_and_version_is_new():
    boards = release.validate(DB)
    types = [int(entry['firmware_image_type']) for entry in boards.values()]
    assert len(types) == len(set(types))
    for entry in boards.values():
        released = release.released_versions(int(entry['firmware_image_type']))
        assert released, 'upstream has released every board in the set'
        assert max(released) < release.RELEASE['version']


def test_shared_image_type_or_old_version_is_refused(tmp_path):
    db = copy.deepcopy(DB)
    db['MODULE_SOMETHING_NEW'] = dict(db['MODULE_AVATTO_A_TS0002'])
    with pytest.raises(ReleaseError, match='shared with MODULE_SOMETHING_NEW'):
        release.validate(db)
    index = tmp_path / 'index.json'
    index.write_text(json.dumps([{'fileName': 'x.zigbee', 'imageType': 43528,
                                  'fileVersion': release.RELEASE['version']}]))
    with pytest.raises(ReleaseError, match='not above released'):
        release.validate(DB, indexes=(index,))


def test_live_board_cannot_drop_out_of_the_release():
    db = copy.deepcopy(DB)
    db['SWITCH_BSEED_TS0726_1GANG']['build'] = 'no'
    with pytest.raises(ReleaseError, match='live board SWITCH_BSEED_TS0726_1GANG'):
        release.validate(db)


def test_cli_matrix_vars_and_refusals(capsys):
    assert release.main(['matrix']) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert [row['board'] for row in matrix['include']] == list(release.validate(DB))
    assert {row['board'] for row in matrix['include'] if row['live']} == set(release.LIVE)
    assert release.main(['vars', '--board', 'SWITCH_BSEED_TS0726_1GANG']) == 0
    lines = dict(line.split('=', 1) for line in capsys.readouterr().out.splitlines())
    assert lines['IMAGE_TYPE'] == "'45580'" and lines['FILE_VERSION_HEX'] == "'0x11023020'"
    assert lines['CONFIG_STR'].startswith("'jn2x20tg;TS0726-1-BS;")
    for board in ('SWITCH_BSEED_TS0726_3GANG', 'MODULE_AVATTO_DRY_TS0001', 'BOARD__MHCOZY_TS0004'):
        assert release.main(['vars', '--board', board]) == 2
    assert 'not in this release' in capsys.readouterr().err


def native_ota(expected, *, build=None, version=None, image_type=None, size=0x8000, extra=b''):
    build = (build or expected['build']).encode()
    body = bytearray(size - 4)
    struct.pack_into('<I', body, 2, expected['version'] if version is None else version)
    body[6:12] = b'\x5d\x02KNLT'
    struct.pack_into('<I', body, 24, size)
    record = bytes([len(build)]) + build + extra
    body[0x400:0x400 + len(record)] = record
    firmware = bytes(body) + struct.pack('<I', binascii.crc32(bytes(body)) ^ 0xFFFFFFFF)
    header = struct.pack('<IHHHHHIH32sI', release.OTA_MAGIC, 0x100, 56, 0, expected['manufacturer'],
                         expected['type'] if image_type is None else image_type,
                         expected['version'], 2, b'Telink OTA Image'.ljust(32, b'\0'),
                         56 + 6 + len(firmware))
    return header + struct.pack('<HI', 0, len(firmware)) + firmware


def corrupt(data, offset):
    out = bytearray(data)
    out[offset] ^= 1
    return bytes(out)


def test_native_verifier_accepts_the_release_identity():
    expected = release.candidate(DB, 'MODULE_AVATTO_A_TS0001')
    assert release.verify_native_image(native_ota(expected), expected)['basicBuildIdVerified']


@pytest.mark.parametrize('fault, message', [
    ('image_type', 'transport identity'),
    ('embedded_version', 'embedded version'),
    ('crc', 'CRC'),
    ('build_id', 'Basic build ID'),
    ('stray', 'stray release build IDs'),
    ('too_big', 'a slot holds'),
    ('second_marker', 'second start marker'),
    ('total_size', 'total size'),
])
def test_native_verifier_refuses_wrong_bytes(fault, message):
    expected = release.candidate(DB, 'MODULE_AVATTO_A_TS0002')
    data = {
        'image_type': lambda: native_ota(expected, image_type=43527),
        'embedded_version': lambda: native_ota(expected, version=0x11023000),
        'crc': lambda: corrupt(native_ota(expected), 62 + 0x2000),
        'build_id': lambda: native_ota(expected, build='1.1.2-anx2'),
        'stray': lambda: native_ota(expected, extra=b'\x0a1.1.2-anx0'),
        'too_big': lambda: native_ota(expected, size=0x34000),
        'second_marker': lambda: None,
        'total_size': lambda: native_ota(expected) + b'\0',
    }[fault]()
    if fault == 'second_marker':
        good = bytearray(native_ota(expected, size=0x21000))
        firmware = bytearray(good[62:-4])
        firmware[0x20008:0x2000C] = b'KNLT'
        firmware += struct.pack('<I', binascii.crc32(bytes(firmware)) ^ 0xFFFFFFFF)
        data = bytes(good[:62]) + bytes(firmware)
    with pytest.raises(ReleaseError, match=message):
        release.verify_native_image(data, expected)


def test_build_script_and_workflow_gate_every_board():
    script = (ROOT / 'make_scripts' / 'build_analienx_board.sh').read_text(encoding='utf-8')
    assert 'analienx_release.py vars --board' in script
    assert 'analienx_release.py verify --board' in script
    assert 'DEVICE_TYPE=router' in script and 'BSEED_LINK_POLICY=1' in script
    assert 'from_tuya' not in script.replace('No from_tuya wrapper', '')
    workflow = (ROOT / '.github' / 'workflows' / 'analienx-release.yml').read_text(encoding='utf-8')
    for needle in ('analienx_release.py matrix', 'verify_boot(', 'verify_activation(',
                   'release.verify_native_image(', 'fromJSON(needs.release-set.outputs.matrix)',
                   'no boot report'):
        assert needle in workflow, needle
