"""AVATTO ZWSM16 Router release identities and native image verification.

Analienx firmware (based on Romasku) for the AVATTO ZWSM16-1/-2 switch modules.
The live modules run upstream Romasku 1.1.2-8542fc05, whose OTA file version is
0x11023000. A generic build of this repository would carry the same version,
so Zigbee2MQTT would never offer it: these candidates use explicit, higher
versions on the boards' own image types (43527, 43528).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bseed_ota_identity import IdentityError  # noqa: E402
from bseed_pm_release import verify_native_image  # noqa: E402

MANUFACTURER_CODE = 4417
INSTALLED_UPSTREAM_VERSION = 0x11023000  # Romasku 1.1.2-8542fc05 on both boards

CANDIDATES = {
    'ts0001': {'board': 'MODULE_AVATTO_A_TS0001', 'manufacturer': '4rbqgcuv',
               'model': 'TS0001-AVB', 'type': 43527, 'role': 'Router',
               'build': '1.1.2-avlp1', 'version': 0x11023001, 'date': '20261102'},
    'ts0002': {'board': 'MODULE_AVATTO_A_TS0002', 'manufacturer': 'mtnpt6ws',
               'model': 'TS0002-AVB', 'type': 43528, 'role': 'Router',
               'build': '1.1.2-avlp1', 'version': 0x11023001, 'date': '20261102'},
}


def validate_candidates():
    for key, c in CANDIDATES.items():
        if not c['build'].isascii() or not 1 <= len(c['build']) <= 16:
            raise IdentityError('%s: Basic build ID must be 1..16 ASCII bytes' % key)
        if c['version'] <= INSTALLED_UPSTREAM_VERSION:
            raise IdentityError('%s: version must exceed the installed upstream '
                                '0x%08X or Zigbee2MQTT never offers it' %
                                (key, INSTALLED_UPSTREAM_VERSION))
    if len({c['type'] for c in CANDIDATES.values()}) != len(CANDIDATES):
        raise IdentityError('each AVATTO board needs its own OTA image type')


def verify(path, key):
    """Check the produced bytes, not just the manifest's claims."""
    validate_candidates()
    c = CANDIDATES[key]
    data = Path(path).read_bytes()
    # Header, firmware subelement, startup marker, embedded version, CRC and
    # the length-prefixed Basic build ID; skips only the BSEED string scan.
    verify_native_image(data, c, bseed_build_strings=False)
    return c


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('vars', help='print shell assignments for one board')
    v.add_argument('--variant', choices=sorted(CANDIDATES), required=True)
    c = sub.add_parser('verify', help='verify a produced forward OTA image')
    c.add_argument('--variant', choices=sorted(CANDIDATES), required=True)
    c.add_argument('--ota', required=True)
    a = p.parse_args(argv)
    validate_candidates()
    cand = CANDIDATES[a.variant]
    if a.cmd == 'vars':
        print("BOARD='%s'" % cand['board'])
        print("SW_BUILD='%s'" % cand['build'])
        print("FILE_VERSION_HEX='0x%08X'" % cand['version'])
        print("FILE_VERSION_DEC=%d" % cand['version'])
        print("IMAGE_TYPE=%d" % cand['type'])
        print("MANUFACTURER_CODE=%d" % MANUFACTURER_CODE)
        print("BUILD_DATE='%s'" % cand['date'])
        return 0
    verify(a.ota, a.variant)
    print('AVATTO %s native image verified: %s 0x%08X type %d'
          % (a.variant, cand['build'], cand['version'], cand['type']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
