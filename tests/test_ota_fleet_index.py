"""The native Zigbee2MQTT fleet index may only offer accepted, normal-version images.

Zigbee2MQTT shows every matching index entry as "Update available". A stock
from_tuya conversion, FORCE or max-version (0xFFFFFFFF) entry would therefore
offer custom firmware to stock devices or force-flash custom ones. Entries are
added only after hardware acceptance of that exact image.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'zigbee2mqtt/ota/index_fleet.json'
REGISTRY = ROOT / 'zigbee2mqtt/ota/bseed_identity.json'
REQUIRED = {'fileVersion', 'imageType', 'manufacturerCode', 'url', 'sha512', 'fileSize'}


def test_fleet_index_offers_only_normal_registered_images():
    entries = json.loads(INDEX.read_text(encoding='utf-8'))
    assert isinstance(entries, list)
    registry = json.loads(REGISTRY.read_text(encoding='utf-8'))
    sealed = {(int(line['image_type']), v['file_version']): v
              for line in registry['lines'] for v in line['versions']}
    seen = set()
    for e in entries:
        assert REQUIRED <= set(e), e
        url = e['url'].lower()
        assert 'from_tuya' not in url and 'forced' not in url and 'force' not in url, url
        assert e['fileVersion'] != 0xFFFFFFFF, url
        assert e['imageType'] != 54179, 'stock Tuya image type: %s' % url
        assert url.startswith('https://raw.githubusercontent.com/analienx/'), url
        key = (e['imageType'], e['fileVersion'])
        assert key not in seen, 'duplicate offer %s' % (key,)
        seen.add(key)
        if e['manufacturerCode'] == 4417 and key in sealed:
            assert sealed[key].get('sha512') == e['sha512'], 'hash differs from sealed identity'
            assert sealed[key].get('status') not in ('retired', 'superseded'), key
