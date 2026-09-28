"""Campaign board/role/version policy derived from the sealed OTA registry.

Transport headers identify the query being answered. Native payload headers
and registry lines identify the role being installed; these are not interchangeable.
"""
import hashlib
from pathlib import Path
import struct

from bseed_ota_identity import DEFAULT_REGISTRY, IdentityError, load_registry, parse_ota_header, gate_image

BOARDS = {
    'b28wrpvx': dict(model='TS011F-BS-PM', stock_prefix='_TZ3000_', stock_model='TS011F',
                     roles=('Router', 'EndDevice')),
    'o1jzcxou': dict(model='TS011F-BS', stock_prefix='_TZ3000_', stock_model='TS011F',
                     roles=('Router', 'EndDevice')),
    'iedhxgyi': dict(model='TS0726-3-BS', stock_prefix='_TZ3002_', stock_model='TS0726',
                     roles=('Router',)),
}
ROLES = {'Router': 'router', 'EndDevice': 'client'}
FORCE_TEST_VERSION = 0xffffffff


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def split_manufacturer(manufacturer):
    """Return (board, stock_prefix or None); stock prefixes are Tuya-owned."""
    for prefix in ('_TZ3000_', '_TZ3002_'):
        if manufacturer.startswith(prefix):
            return manufacturer[len(prefix):], prefix
    return manufacturer, None


def require_increasing(profile, registry=None):
    registry = load_registry(DEFAULT_REGISTRY) if registry is None else registry
    if 'force_test_transition' in profile and profile['force_test_transition'] is not True and profile['force_test_transition'] is not False:
        raise IdentityError('force_test_transition must be an explicit Boolean')
    force_test = profile.get('force_test_transition') is True
    manufacturer = profile.get('manufacturer', '')
    board, stock_prefix = split_manufacturer(manufacturer)
    spec = BOARDS.get(board)
    if spec is None or (stock_prefix is not None and stock_prefix != spec['stock_prefix']):
        raise IdentityError('campaign board/model is not a supported BSEED device')
    expected_model = spec['model'] if stock_prefix is None else spec['stock_model']
    if profile.get('model') != expected_model:
        raise IdentityError('campaign board/model is not a supported BSEED device')
    source_role, target_role = profile.get('preflash_role'), profile.get('postflash_role')
    if source_role not in spec['roles'] or target_role not in spec['roles']:
        raise IdentityError('campaign role is missing or unsupported')

    def line_for(role):
        lines = [(typ, line) for typ, line in registry.items()
                 if line.get('board_key') == board and line.get('role') == ROLES[role]
                 and not line.get('shared_identity')]
        if len(lines) != 1:
            raise IdentityError('ambiguous or missing board/role registry line')
        return lines[0]

    source_type, source_line = line_for(source_role)
    target_type, target_line = line_for(target_role)
    build = profile.get('postflash_build', '')
    if not build.isascii() or not 1 <= len(build) <= 16:
        raise IdentityError('postflash Basic identity must fit 16 ASCII bytes without truncation')
    transport = Path(profile['image']).read_bytes()
    if hashlib.sha256(transport).hexdigest() != profile['sha256']:
        raise IdentityError('campaign image hash mismatch')
    payload = Path(profile.get('native_image') or profile['image']).read_bytes()
    if force_test:
        if not profile.get('native_image') or not profile.get('native_sha256'):
            raise IdentityError('force-test transition requires native_image and native_sha256')
        if hashlib.sha256(payload).hexdigest() != profile['native_sha256']:
            raise IdentityError('force-test native image hash mismatch')
    outer, native = parse_ota_header(transport), parse_ota_header(payload)
    if (outer['manufacturer_code'] != 4417 or outer['manufacturer_code'] != number(profile['manufacturer_code']) or
            outer['image_type'] != number(profile['image_type']) or
            outer['file_version'] != number(profile['file_version'])):
        raise IdentityError('campaign transport tuple mismatch')
    if native['manufacturer_code'] != 4417 or native['image_type'] != target_type:
        raise IdentityError('campaign payload has the wrong board/role')
    if transport[56:] != payload[56:]:
        raise IdentityError('campaign wrapper changed native payload')
    target = [e for e in target_line['versions'] if e['file_version'] == native['file_version']
              and e.get('version_str') == profile.get('postflash_build')]
    if len(target) != 1 or not target[0].get('sha512'):
        raise IdentityError('campaign target must be a sealed build/role/version')
    if target[0].get('payload_role', target_role) != target_role or target[0].get('payload_image_type', target_type) != target_type:
        raise IdentityError('transport wrapper registry entry is not native firmware for the requested role')
    matching_roles = {line.get('role') for line in registry.values() if line.get('board_key') == board
                      and not line.get('shared_identity') and any(
                          e.get('version_str') == build and e.get('file_version') == native['file_version']
                          for e in line.get('versions', []))}
    if len(matching_roles) > 1 and 'payload_role' not in target[0]:
        raise IdentityError('ambiguous wrapper/native identity needs explicit sealed payload role metadata')
    gate_image(payload, registry, profile['postflash_build'], target_type, native['file_version'])

    if stock_prefix is not None:
        # Stock versions use a different numbering scheme. Stock conversion
        # is only the separately validated stock wrapper into a custom Router.
        if force_test:
            raise IdentityError('force-test transition is only for already-custom socket firmware')
        if source_role != 'Router' or target_role != 'Router' or outer['image_type'] != 54179 or outer['file_version'] != 0xffffffff:
            raise IdentityError('stock conversion requires stock-wrapper to Router')
        return {'board': board, 'source': 'stock', 'target_version': native['file_version']}

    source = [e for e in source_line['versions'] if e.get('version_str') == profile.get('preflash_build')]
    if 'preflash_file_version' in profile:
        source = [e for e in source if e['file_version'] == number(profile['preflash_file_version'])]
    if len(source) != 1:
        raise IdentityError('pin an unambiguous preflash build/role/version from live evidence')
    if source[0].get('payload_role', source_role) != source_role:
        raise IdentityError('source role contradicts the registered payload role')
    query_type = number(profile.get('preflash_query_image_type', source_type))
    board_types = {typ for typ, line in registry.items()
                   if line.get('board_key') == board and line.get('role') in ROLES.values()
                   and not line.get('shared_identity')}
    if query_type not in board_types:
        raise IdentityError('query type belongs to a different board or unsupported transport')

    if force_test:
        if board not in ('b28wrpvx', 'o1jzcxou') or source_role == target_role:
            raise IdentityError('force-test mode is limited to cross-role BSEED socket transitions')
        if outer['image_type'] != query_type or outer['file_version'] != FORCE_TEST_VERSION:
            raise IdentityError('force-test wrapper must answer the source query type at 0xFFFFFFFF')
        expected = bytearray(payload)
        struct.pack_into('<H', expected, 12, query_type)
        struct.pack_into('<I', expected, 14, FORCE_TEST_VERSION)
        if transport != bytes(expected):
            raise IdentityError('force-test wrapper may change only outer image type and file version')
        return {'board': board, 'source_version': source[0]['file_version'],
                'target_version': native['file_version'], 'source_role': source_role,
                'target_role': target_role, 'force_test_transition': True}

    if native['file_version'] <= source[0]['file_version']:
        raise IdentityError('target must be strictly newer across both roles; equal-version transitions are no-ops')
    if outer['image_type'] != query_type or outer['file_version'] != native['file_version']:
        raise IdentityError('custom update must answer the pinned query type at the native new version')
    return {'board': board, 'source_version': source[0]['file_version'],
            'target_version': native['file_version'], 'source_role': source_role, 'target_role': target_role}
