"""The native fleet index offers only released, sealed, same-role images."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from ota_fleet_index import INDEX, REGISTRY, FleetIndexError, validate  # noqa: E402

URL = 'https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/bseed/x.ota'


def registry():
    return {'lines': [
        {'image_type': 43556, 'role': 'router', 'board_key': 'b28wrpvx', 'versions': [
            {'file_version': 100, 'version_str': 'r-ok', 'sha512': 'a' * 128,
             'file_size': 1000, 'status': 'released'},
            {'file_version': 101, 'version_str': 'r-canary', 'sha512': 'b' * 128,
             'status': 'canary'},
            {'file_version': 102, 'version_str': 'r-nohash', 'sha512': None,
             'status': 'released'},
            {'file_version': 103, 'version_str': 'r-plain', 'sha512': 'c' * 128}]},
        {'image_type': 65024, 'role': 'client', 'board_key': 'b28wrpvx', 'versions': [
            {'file_version': 104, 'version_str': 'cross', 'sha512': 'd' * 128,
             'status': 'released', 'payload_role': 'Router', 'payload_image_type': 43556}]},
        {'image_type': 54179, 'role': 'wrapper', 'shared_identity': True, 'versions': [
            {'file_version': 0xFFFFFFFF, 'version_str': 'w', 'sha512': 'e' * 128,
             'status': 'released'}]},
    ]}


def good():
    return {'fileVersion': 100, 'imageType': 43556, 'manufacturerCode': 4417,
            'url': URL, 'sha512': 'a' * 128, 'fileSize': 1000,
            'manufacturerName': ['b28wrpvx']}


def test_shipped_index_and_registry_validate():
    validate(json.loads(INDEX.read_text(encoding='utf-8')),
             json.loads(REGISTRY.read_text(encoding='utf-8')))


def test_released_sealed_same_role_entry_is_accepted():
    assert validate([good()], registry())


@pytest.mark.parametrize('change, message', [
    ({'fileVersion': 101, 'sha512': 'b' * 128}, 'not released'),     # canary
    ({'fileVersion': 103, 'sha512': 'c' * 128}, 'not released'),     # no status
    ({'fileVersion': 102, 'sha512': None}, 'sha512'),                # null hash
    ({'sha512': 'f' * 128}, 'sha512'),                               # wrong bytes
    ({'fileSize': 999}, 'fileSize'),
    ({'fileVersion': 999}, 'not a registered'),                      # unregistered
    ({'imageType': 65024, 'fileVersion': 104, 'sha512': 'd' * 128}, 'cross-role'),
    ({'imageType': 54179, 'fileVersion': 0xFFFFFFFF, 'sha512': 'e' * 128}, 'conversion|not a registered'),
    ({'imageType': 12345}, 'not a registered'),                      # foreign stock type
    ({'force': True}, 'bypass'),
    ({'minFileVersion': 1}, 'bypass'),
    ({'maxFileVersion': 99}, 'bypass'),
    ({'url': URL.replace('analienx', 'romasku')}, 'foreign URL'),
    ({'url': URL.replace('x.ota', 'x-from_tuya.ota')}, 'conversion'),
    ({'url': URL.replace('x.ota', 'x-FORCE.ota')}, 'conversion'),
    ({'manufacturerName': []}, 'manufacturers'),
])
def test_every_bypass_is_refused(change, message):
    entry = dict(good(), **change)
    with pytest.raises(FleetIndexError, match=message):
        validate([entry], registry())


def test_missing_fields_and_duplicates_are_refused():
    entry = good()
    del entry['manufacturerName']
    with pytest.raises(FleetIndexError, match='missing'):
        validate([entry], registry())
    with pytest.raises(FleetIndexError, match='duplicate'):
        validate([good(), copy.deepcopy(good())], registry())
