"""Immutable inputs for the consolidated non-PM Router/Client candidates."""
import argparse

from bseed_ota_identity import IdentityError

RELEASE_DATE = '20261002'
BOARD = 'OUTLET_BSEED_TS011F'
CONFIG = 'o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'
# Fresh shared version allocated by the canonical allocator on GitHub Actions.
ROUTER = dict(role='Router', build='1.1.3-bseedr11', version=0x11023015, type=43555,
              artifact='forward.ota', board=BOARD, config=CONFIG, date=RELEASE_DATE)
CLIENT = dict(role='EndDevice', build='1.1.3-bseedc8', version=0x11023015, type=65026,
              artifact='forward.ota', board=BOARD, config=CONFIG, date=RELEASE_DATE)
CANDIDATES = {'router': ROUTER, 'client': CLIENT}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['vars'])
    parser.add_argument('--role', choices=CANDIDATES, required=True)
    args = parser.parse_args()
    item = CANDIDATES[args.role]
    if not item['build'].isascii() or not 1 <= len(item['build']) <= 16:
        raise IdentityError('Basic build ID must be 1..16 ASCII bytes')
    print(item['build'])
    print(hex(item['version']))
    print(item['version'])
    print(RELEASE_DATE)


if __name__ == '__main__':
    main()
