"""Public CI verifies fresh identities using the canonical allocator."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_ota_identity import emit_make_vars
from bseed_pm_release import CLIENT, ROUTER
from bseed_nonpm_release import CANDIDATES as NONPM


def test_candidates_follow_canonical_board_allocator():
    document = json.loads((ROOT / 'zigbee2mqtt/ota/bseed_identity.json').read_text())
    registry = {int(line['image_type']): copy.deepcopy(line) for line in document['lines']}

    # Shared-source PM roles get a common new version on distinct image
    # types, matching the historical PM paired-release policy.
    assert ROUTER['version'] == CLIENT['version']
    local = copy.deepcopy(registry)
    for candidate in (ROUTER, CLIENT):
        line = local[candidate['type']]
        line['versions'] = [v for v in line['versions']
                            if v['file_version'] != candidate['version']]
    for candidate in (ROUTER, CLIENT):
        allocation = emit_make_vars(local, candidate['type'], candidate['build'])
        assert int(allocation['FILE_VERSION'], 0) == candidate['version']
        assert allocation['VERSION_STR'] == candidate['build']

    local = copy.deepcopy(registry)
    assert NONPM['router']['version'] == NONPM['client']['version']
    for candidate in NONPM.values():
        line = local[candidate['type']]
        line['versions'] = [v for v in line['versions']
                            if v['file_version'] != candidate['version']]
    for candidate in NONPM.values():
        allocation = emit_make_vars(local, candidate['type'], candidate['build'])
        assert int(allocation['FILE_VERSION'], 0) == candidate['version']
        assert allocation['VERSION_STR'] == candidate['build']

    # The independently versioned TS0726 targets remain unchanged.
    for role, ver in ((45577, 0x1102300F), (65025, 0x1102300F)):
        assert ver > max(v['file_version'] for v in registry[role]['versions'])


def test_retired_pm_return_tuple_stays_reserved_without_being_a_candidate():
    document = json.loads((ROOT / 'zigbee2mqtt/ota/bseed_identity.json').read_text())
    retired = [
        entry
        for line in document['lines']
        if line.get('board_key') == 'b28wrpvx'
        for entry in line.get('versions', [])
        if entry.get('file_version') == 0x12053017
        and entry.get('version_str') == '1.2.5-bseedr10'
    ]
    assert len(retired) == 2 and all(entry.get('status') == 'retired' for entry in retired)
    registry = {int(line['image_type']): copy.deepcopy(line) for line in document['lines']}
    next_router = emit_make_vars(registry, ROUTER['type'], '1.2.5-bseedr15')
    assert int(next_router['FILE_VERSION'], 0) == CLIENT['version'] + 1
