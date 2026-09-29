"""One PM candidate definition and native-image verifier; no live operations."""
import argparse
import binascii
import datetime
import struct

from bseed_ota_identity import IdentityError, parse_ota_header, require_embedded_string

CLIENT = {'role': 'EndDevice', 'build': '1.2.5-bseedcli12',
          'version': 0x12053016, 'type': 65024, 'artifact': 'forward.ota'}
ROUTER = {'role': 'Router', 'build': '1.2.5-bseedr9',
          'version': 0x12053016, 'type': 43556, 'artifact': 'forward.ota'}
CANDIDATES = {'client': CLIENT, 'router': ROUTER}
RELEASE_DATE = '20260928'  # Immutable input for this candidate set, not wall-clock build time.


def validate_candidate_set():
    if datetime.datetime.strptime(RELEASE_DATE, '%Y%m%d').strftime('%Y%m%d') != RELEASE_DATE:
        raise IdentityError('release date must be YYYYMMDD')
    for item in CANDIDATES.values():
        if not item['build'].isascii() or not 1 <= len(item['build']) <= 16:
            raise IdentityError('candidate Basic build ID must fit 16 ASCII bytes')
    if CLIENT['version'] != ROUTER['version']:
        raise IdentityError('PM Client/Router candidates must share one native version')
    if CLIENT['type'] != 65024 or ROUTER['type'] != 43556:
        raise IdentityError('unexpected PM role image type')


def verify_native_image(data, expected, transport_type=None):
    """Check the downloaded bytes, not just manifest claims or OTA headers."""
    validate_candidate_set()
    if not expected['build'].isascii() or not 1 <= len(expected['build']) <= 16:
        raise IdentityError('native verifier: expected Basic build ID exceeds 16 ASCII bytes')
    header = parse_ota_header(data)
    if (struct.unpack_from('<H', data, 4)[0], struct.unpack_from('<H', data, 8)[0],
            struct.unpack_from('<H', data, 18)[0]) != (0x100, 0, 2):
        raise IdentityError('native verifier: unsupported OTA header format')
    image_type = expected['type'] if transport_type is None else transport_type
    if (header['header_length'], header['manufacturer_code'], header['image_type'],
            header['file_version']) != (56, 4417, image_type, expected['version']):
        raise IdentityError('native verifier: wrong transport identity')
    if len(data) < 94:
        raise IdentityError('native verifier: truncated firmware')
    tag, size = struct.unpack_from('<HI', data, 56)
    fw = data[62:]
    if tag != 0 or size != len(fw):
        raise IdentityError('native verifier: bad firmware subelement')
    if fw[6:12] != b'\x5d\x02KNLT':
        raise IdentityError('native verifier: bad startup/CRC marker')
    if struct.unpack_from('<I', fw, 2)[0] != expected['version']:
        raise IdentityError('native verifier: embedded version differs from OTA version')
    if struct.unpack_from('<I', fw, 24)[0] != len(fw):
        raise IdentityError('native verifier: firmware length mismatch')
    if struct.unpack_from('<I', fw, len(fw)-4)[0] != (binascii.crc32(fw[:-4]) ^ 0xffffffff):
        raise IdentityError('native verifier: CRC mismatch')
    require_embedded_string(fw, expected['build'])
    build = expected['build'].encode('ascii')
    if bytes([len(build)]) + build not in fw:
        raise IdentityError('native verifier: missing length-prefixed Basic build ID')
    return {'nativeIntegrityVerified': True, 'basicBuildIdVerified': True,
            'hardwareAcceptance': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['vars'])
    p.add_argument('--role', choices=CANDIDATES, required=True)
    a = p.parse_args()
    validate_candidate_set()
    item = CANDIDATES[a.role]
    # Four values consumed as an array by bash, never evaluated as shell code.
    print(item['build'])
    print(hex(item['version']))
    print(item['version'])
    print(RELEASE_DATE)


if __name__ == '__main__':
    main()
