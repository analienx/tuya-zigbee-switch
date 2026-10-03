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
    groups = [[ROUTER, CLIENT], list(NONPM.values()), [
        {'type': 45577, 'version': 0x1102300F, 'build': '1.1.9-bseedlv3'},
        {'type': 65025, 'version': 0x1102300F, 'build': '1.1.8-bseedcli3'}]]
    # Replay the original allocation point for the current candidates. The
    # later PM .17 return experiment is now a retired tombstone, not a release,
    # but remains reserved in the real registry so future allocations skip it.
    pairs = {(c['type'], c['version']) for group in groups for c in group}
    for image_type, line in registry.items():
        line['versions'] = [v for v in line['versions'] if (image_type, v['file_version']) not in pairs]
    for group in groups:
        for candidate in group:
            allocation = emit_make_vars(registry, candidate['type'], candidate['build'])
            assert int(allocation['FILE_VERSION'], 0) == candidate['version']
            assert allocation['VERSION_STR'] == candidate['build']
        for candidate in group:
            registry[candidate['type']]['versions'].append({
                'file_version': candidate['version'], 'version_str': candidate['build']})



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
    for line in registry.values():
        line['versions'] = [v for v in line['versions'] if
                            v['file_version'] != ROUTER['version']]
    next_router = emit_make_vars(registry, ROUTER['type'], ROUTER['build'])
    assert int(next_router['FILE_VERSION'], 0) == ROUTER['version']
