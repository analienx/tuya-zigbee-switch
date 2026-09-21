#!/usr/bin/env python3
"""Read-only, four-variant native BSEED socket artifact gate (never flashes)."""
import hashlib
import json
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Label, artifact directory, immutable board config, role, build ID,
# file version, OTA image type, previous deployed/canary version, wrapper type.
CASES = (
    ('PM Router', 'bseed-ts011f-pm-router-v8u5-rc3',
     'b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;', 'Router',
     '1.2.5-bseedv8u5-rc3', 302329872, 43556, 302329871, 54179),
    ('PM Client', 'bseed-ts011f-pm-client-antibrick-r2',
     'b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;', 'EndDevice',
     '1.2.5-bseedcli8', 302329873, 65024, 302329872, 43556),
    ('non-PM Router', 'bseed-ts011f-nonpm-router-antibrick-r2',
     'o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;', 'Router',
     '1.1.3-bseedv10', 285356052, 43555, 285356051, 54179),
    ('non-PM Client', 'bseed-ts011f-nonpm-client-antibrick-r2',
     'o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;', 'EndDevice',
     '1.1.2-bseedcli7', 285356051, 65026, 285356050, 43555),
)


def ota_header(data: bytes) -> tuple[int, int, int]:
    if len(data) < 56:
        raise ValueError('truncated OTA image')
    magic, hv, hl, fc, manufacturer, image_type, version, stack, text, size = (
        struct.unpack_from('<I5HIH32sI', data)
    )
    assert magic == 0x0BEEF11E and hl == 56 and size == len(data)
    assert manufacturer == 4417 and hv == 0x0100 and stack == 2
    return manufacturer, image_type, version


def verify() -> None:
    from bseed_socket_version_policy import BOARDS, CANDIDATES, require_increasing
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip(), 'source is dirty'
    for label, directory, config, role, build, version, image_type, previous, wrapper_type in CASES:
        folder = ROOT / 'build' / directory
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['sourceCommit'] == commit and manifest['sourceDirty'] is False, label
        assert manifest['canonicalConfig'] == config and manifest['fileVersion'] == version, label
        assert version > previous and manifest['manufacturerCode'] == 4417, label
        board_id = 'b28wrpvx' if label.startswith('PM') else 'o1jzcxou'
        model = BOARDS[board_id][0]
        assert build in CANDIDATES, label
        assert BOARDS[board_id][1][build] == version, label
        assert previous in BOARDS[board_id][1].values(), label
        assert version not in (v for name, v in BOARDS[board_id][1].items() if name != build), label
        assert len({r[5] for r in CASES if r[0].startswith('PM') == label.startswith('PM')}) == 2, label
        assert manifest.get('swBuildId', manifest.get('softwareBuild')) == build, label
        assert manifest['board'] == ('OUTLET_BSEED_PM_TS011F' if label.startswith('PM') else 'OUTLET_BSEED_TS011F'), label
        if role == 'EndDevice':
            assert manifest['role']['logicalType'] == 'end-device' and manifest['role']['rxOnWhenIdle'] is True
            assert manifest['distribution']['normalOtaIndex'] is False
        else:
            assert manifest['imageType'] == image_type
        native = (folder / 'forward.bin').read_bytes()
        forward = (folder / 'forward.ota').read_bytes()
        wrapper_name = 'from-router.ota' if role == 'EndDevice' else 'from_tuya.ota'
        wrapper = (folder / wrapper_name).read_bytes()
        assert ota_header(forward) == (4417, image_type, version), label
        assert ota_header(wrapper) == (4417, wrapper_type,
                                        version if role == 'EndDevice' else 0xFFFFFFFF), label
        assert forward[56:] == wrapper[56:], label + ': wrapper changes payload'
        assert len(native) > 1000 and len(forward) > len(native), label
        if 'artifacts' in manifest:
            for filename, meta in manifest['artifacts'].items():
                contents = (folder / filename).read_bytes()
                assert len(contents) == meta['bytes'], (label, filename, 'size')
                for algorithm in ('sha256', 'sha512'):
                    assert hashlib.new(algorithm, contents).hexdigest() == meta[algorithm], (label, filename, algorithm)
        else:
            assert hashlib.sha256(native).hexdigest() == manifest['forwardBinSha256'], label
            assert hashlib.sha256(forward).hexdigest() == manifest['forwardOta']['sha256'], label
            assert hashlib.sha256(wrapper).hexdigest() == manifest['fromTuyaOtaHeader']['sha256'], label
        print(f'PASS {label}: {build} {version:#010x}, OTA SHA256={hashlib.sha256(forward).hexdigest()}')
    print('OFFLINE_ARTIFACT_GATE_PASS: four board/role candidates; no OTA authorized by this result')


if __name__ == '__main__':
    verify()
