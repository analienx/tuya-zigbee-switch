"""Create a private 0xFFFFFFFF OTA wrapper around an already-sealed BSEED image.

This is a test transport only. It never builds firmware, publishes an index or
contacts a device. The payload bytes after the OTA header remain exact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from bseed_ota_identity import (
    DEFAULT_REGISTRY, IdentityError, gate_image, load_registry, parse_ota_header,
)
from bseed_socket_version_policy import BOARDS, FORCE_TEST_VERSION, ROLES

ROOT = Path(__file__).resolve().parents[1]


def role_line(registry, board, role):
    matches = [(image_type, line) for image_type, line in registry.items()
               if line.get('board_key') == board
               and line.get('role') == ROLES[role]
               and not line.get('shared_identity')]
    if len(matches) != 1:
        raise IdentityError('ambiguous or missing board/role registry line')
    return matches[0]


def build_force_wrapper(native, *, board, source_role, target_role, target_build, registry):
    if board not in ('b28wrpvx', 'o1jzcxou') or board not in BOARDS:
        raise IdentityError('force-test wrappers are limited to BSEED socket boards')
    if source_role not in BOARDS[board]['roles'] or target_role not in BOARDS[board]['roles']:
        raise IdentityError('unsupported source or target role')
    if source_role == target_role:
        raise IdentityError('force-test wrapper requires a cross-role transition')

    source_type, _ = role_line(registry, board, source_role)
    target_type, target_line = role_line(registry, board, target_role)
    header = parse_ota_header(native)
    if header['manufacturer_code'] != 4417 or header['image_type'] != target_type:
        raise IdentityError('native image does not match requested target board/role')
    entries = [entry for entry in target_line.get('versions', [])
               if int(entry['file_version']) == header['file_version']
               and entry.get('version_str') == target_build]
    if len(entries) != 1 or not entries[0].get('sha512'):
        raise IdentityError('force-test target must be an already-sealed candidate')
    gate_image(native, registry, target_build, target_type, header['file_version'])

    wrapper = bytearray(native)
    struct.pack_into('<H', wrapper, 12, source_type)
    struct.pack_into('<I', wrapper, 14, FORCE_TEST_VERSION)
    wrapped = bytes(wrapper)
    if wrapped[56:] != native[56:]:
        raise IdentityError('force-test wrapper changed native payload')
    return wrapped, {
        'board': board,
        'sourceRole': source_role,
        'targetRole': target_role,
        'targetBuild': target_build,
        'nativeFileVersion': header['file_version'],
        'nativeImageType': target_type,
        'wrapperImageType': source_type,
        'wrapperFileVersion': FORCE_TEST_VERSION,
        'nativeSha256': hashlib.sha256(native).hexdigest(),
        'wrapperSha256': hashlib.sha256(wrapped).hexdigest(),
        'payloadIdentical': True,
        'forceTestOnly': True,
        'deploymentReady': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--board', choices=('b28wrpvx', 'o1jzcxou'), required=True)
    parser.add_argument('--source-role', choices=('Router', 'EndDevice'), required=True)
    parser.add_argument('--target-role', choices=('Router', 'EndDevice'), required=True)
    parser.add_argument('--target-build', required=True)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error('force-test wrapper must be private and outside the repository')
    if output.exists():
        parser.error('refusing to overwrite an existing force-test wrapper')
    native = args.native.expanduser().resolve().read_bytes()
    wrapped, report = build_force_wrapper(
        native,
        board=args.board,
        source_role=args.source_role,
        target_role=args.target_role,
        target_build=args.target_build,
        registry=load_registry(args.registry),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as handle:
        handle.write(wrapped)
    report['output'] = str(output)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
