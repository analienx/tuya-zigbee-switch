"""Public CI verifies fresh identities using the canonical allocator."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_ota_identity import emit_make_vars
from bseed_pm_release import CLIENT, ROUTER, RETURN
from bseed_nonpm_release import CANDIDATES as NONPM


def test_candidates_follow_canonical_board_allocator():
    document = json.loads((ROOT / 'zigbee2mqtt/ota/bseed_identity.json').read_text())
    registry = {int(line['image_type']): copy.deepcopy(line) for line in document['lines']}
    groups = [[ROUTER, CLIENT], list(NONPM.values()), [
        {'type': 45577, 'version': 0x1102300F, 'build': '1.1.9-bseedlv3'},
        {'type': 65025, 'version': 0x1102300F, 'build': '1.1.8-bseedcli3'}], [RETURN]]
    # Remove only this candidate set from the in-memory allocator baseline.
    # This test remains valid after CI seals the same immutable tuples.
    pairs = {(c['type'], c['version']) for group in groups for c in group}
    pairs.add((65024, RETURN['version']))  # return transport wrapper
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
