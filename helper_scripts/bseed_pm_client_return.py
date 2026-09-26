"""Build and verify an offline-only PM Client -> Router candidate on CI.

No MQTT, index publication, campaign unlock, or device write. Valid packaging
does not establish that cli8/cli10 can apply the image or that Router boots.
"""
import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct

from bseed_ota_identity import DEFAULT_REGISTRY, gate_image, load_registry, parse_ota_header
from bseed_pm_variant_matrix import ROOT, run, verify_artifact

RETURN = {'role': 'Router', 'build': '1.2.5-bseedv8u5-rc6',
          'version': 0x12053013, 'type': 43556, 'artifact': 'forward.ota'}
CLIENT_VERSION = 0x12053012


def verify_return(native, wrapper, registry, installed_version=CLIENT_VERSION):
    """Check wire identity, exact payload, native startup/length/version/CRC."""
    nh = parse_ota_header(native)
    wh = parse_ota_header(wrapper)
    for h, image_type in ((nh, 43556), (wh, 65024)):
        if (h['header_length'], h['manufacturer_code'], h['image_type'],
                h['file_version']) != (56, 4417, image_type, RETURN['version']):
            raise ValueError('unexpected PM return OTA header')
    if wh['file_version'] <= installed_version:
        raise ValueError('return version must exceed the installed Client version')
    if len(native) != len(wrapper) or native[56:] != wrapper[56:]:
        raise ValueError('return wrapper changed the Router payload')
    if [i for i, (a, b) in enumerate(zip(native, wrapper)) if a != b] != [12, 13]:
        raise ValueError('return wrapper must change only image-type bytes')
    if len(native) < 94:
        raise ValueError('truncated native firmware')
    tag, size = struct.unpack_from('<HI', native, 56)
    fw = native[62:]
    if tag != 0 or size != len(fw):
        raise ValueError('invalid native firmware subelement')
    if fw[6:8] != b'\x5d\x02' or fw[8:12] != b'KNLT':
        raise ValueError('invalid Telink startup/CRC marker')
    if struct.unpack_from('<I', fw, 2)[0] != RETURN['version']:
        raise ValueError('embedded version differs from transport version')
    if struct.unpack_from('<I', fw, 0x18)[0] != len(fw):
        raise ValueError('embedded firmware size mismatch')
    if struct.unpack_from('<I', fw, len(fw)-4)[0] != (binascii.crc32(fw[:-4]) ^ 0xffffffff):
        raise ValueError('native firmware CRC mismatch')
    # Wrapper identities are real OTA tuples too: never collide with cli11 etc.
    for data, image_type in ((native, 43556), (wrapper, 65024)):
        gate_image(data, registry, RETURN['build'], image_type, RETURN['version'])
    return {'sourceRole': 'EndDevice', 'destinationRole': 'Router',
            'minimumTestedSourceBuild': None, 'baselineClientVersion': installed_version,
            'build': RETURN['build'], 'fileVersion': RETURN['version'],
            'wrapperImageType': 65024, 'runningRouterImageType': 43556,
            'payloadIdentical': True, 'nativeIntegrityVerified': True,
            'sha256': hashlib.sha256(wrapper).hexdigest(),
            'sha512': hashlib.sha512(wrapper).hexdigest(),
            'hardwareAcceptance': False, 'applyPathFix': False,
            'deploymentReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='build/bseed-pm-client-return-rc6')
    args = parser.parse_args()
    out = (ROOT / args.output_dir).resolve()
    if not out.is_relative_to((ROOT / 'build').resolve()) or out.exists():
        parser.error('output must be a new directory below ignored build/')
    head = run(['git', 'rev-parse', 'HEAD']).strip()
    if run(['git', 'status', '--porcelain']).strip():
        raise RuntimeError('return build requires clean sources')
    env = dict(os.environ, BSEED_PM_CLIENT_RETURN='1',
               BSEED_PM_CLIENT_RETURN_OUTPUT=str(out))
    run(['bash', 'make_scripts/build_bseed_ts011f_pm_v8.sh'], env=env)
    verify_artifact(out, RETURN, head)
    report = verify_return((out/'forward.ota').read_bytes(),
                           (out/'from-client.ota').read_bytes(),
                           load_registry(DEFAULT_REGISTRY))
    manifest = json.loads((out/'manifest.json').read_text())
    if report['sha256'] != manifest['artifacts']['from-client.ota']['sha256']:
        raise ValueError('wrapper manifest hash mismatch')
    report['sourceCommit'] = head
    (out/'CLIENT_RETURN.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
