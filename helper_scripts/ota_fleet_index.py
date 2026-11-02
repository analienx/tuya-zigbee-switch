"""Validate the curated native Zigbee2MQTT fleet index (allowlist, never denylist).

Zigbee2MQTT offers every matching index entry as "Update available". An entry
is acceptable only when it is a released, hash-sealed, same-role image on its
own custom image type, restricted to named manufacturers, served from the
Analienx repository, and carries no field that bypasses version checks.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'zigbee2mqtt/ota/index_fleet.json'
REGISTRY = ROOT / 'zigbee2mqtt/ota/bseed_identity.json'
URL_PREFIX = 'https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/'
REQUIRED = ('fileVersion', 'imageType', 'manufacturerCode', 'url', 'sha512',
            'fileSize', 'manufacturerName')
FORBIDDEN = ('force', 'minFileVersion', 'maxFileVersion')
ROLE_LINES = {'router', 'client'}


class FleetIndexError(ValueError):
    pass


def sealed_identities(registry):
    out = {}
    for line in registry['lines']:
        if line.get('shared_identity') or line.get('role') not in ROLE_LINES:
            continue
        for v in line['versions']:
            out[(int(line['image_type']), int(v['file_version']))] = (line, v)
    return out


def validate(entries, registry):
    if not isinstance(entries, list):
        raise FleetIndexError('fleet index must be a JSON list')
    sealed = sealed_identities(registry)
    seen = set()
    for e in entries:
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            raise FleetIndexError('entry missing %s' % missing)
        bypass = [k for k in FORBIDDEN if k in e]
        if bypass:
            raise FleetIndexError('version-check bypass fields %s: %s' % (bypass, e['url']))
        url = str(e['url'])
        if not url.startswith(URL_PREFIX):
            raise FleetIndexError('foreign URL %s' % url)
        low = url.lower()
        if 'from_tuya' in low or 'force' in low:
            raise FleetIndexError('conversion or FORCE image %s' % url)
        names = e['manufacturerName']
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
            raise FleetIndexError('entry must name its manufacturers: %s' % url)
        key = (int(e['imageType']), int(e['fileVersion']))
        if key in seen:
            raise FleetIndexError('duplicate offer %s' % (key,))
        seen.add(key)
        if key not in sealed:
            raise FleetIndexError('not a registered custom role identity %s' % (key,))
        line, v = sealed[key]
        if v.get('status') != 'released':
            raise FleetIndexError('identity %s status %r is not released' % (key, v.get('status')))
        if not v.get('sha512') or v['sha512'] != e['sha512']:
            raise FleetIndexError('sha512 differs from sealed identity %s' % (key,))
        if v.get('file_size') is not None and int(v['file_size']) != int(e['fileSize']):
            raise FleetIndexError('fileSize differs from sealed identity %s' % (key,))
        role = {'router': 'Router', 'client': 'EndDevice'}[line['role']]
        if v.get('payload_role', role) != role or int(v.get('payload_image_type', key[0])) != key[0]:
            raise FleetIndexError('cross-role payload %s' % (key,))
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', default=str(INDEX))
    p.add_argument('--registry', default=str(REGISTRY))
    a = p.parse_args(argv)
    validate(json.loads(Path(a.index).read_text(encoding='utf-8')),
             json.loads(Path(a.registry).read_text(encoding='utf-8')))
    print('fleet index valid')
    return 0


if __name__ == '__main__':
    sys.exit(main())
