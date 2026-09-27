import copy
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_ota_identity import IdentityError
from bseed_pm_release import CLIENT, ROUTER, RETURN
from bseed_pm_seal import seal_entries, verify_bundle
from bseed_image_fixture import image_for as ota_image
from test_bseed_pm_variant_matrix import sample_artifact


def registry():
    return {'lines': [{'image_type': c['type'], 'board_key': 'pm', 'versions': [
        {'file_version': c['version'], 'version_str': c['build'], 'sha512': None}
    ]} for c in (CLIENT, ROUTER)]}


def test_seal_records_verified_hash_without_mutating_input_and_is_idempotent():
    original = registry()
    before = copy.deepcopy(original)
    artifacts = [(ota_image(c), c, c['type']) for c in (CLIENT, ROUTER)]
    sealed = seal_entries(original, artifacts, 'a' * 40)
    assert original == before
    for line, (blob, _, _) in zip(sealed['lines'], artifacts):
        assert line['versions'][0]['sha512'] == hashlib.sha512(blob).hexdigest()
    assert seal_entries(sealed, artifacts, 'b' * 40) == sealed


def test_seal_rejects_relabel_and_wrong_reservation_without_mutating_registry():
    data = registry()
    blob = ota_image(CLIENT)
    data['lines'][0]['versions'][0]['version_str'] = '1.2.5-other'
    with pytest.raises(IdentityError, match='reserved build string'):
        seal_entries(data, [(blob, CLIENT, CLIENT['type'])], 'a' * 40)
    data = seal_entries(registry(), [(blob, CLIENT, CLIENT['type'])], 'a' * 40)
    changed = bytearray(blob); changed[-10] ^= 1
    before = copy.deepcopy(data)
    with pytest.raises(IdentityError, match='RELABEL REFUSED'):
        seal_entries(data, [(bytes(changed), CLIENT, CLIENT['type'])], 'a' * 40)
    assert data == before


def bundle(tmp_path):
    matrix = tmp_path / 'matrix'; matrix.mkdir()
    reports = []
    for name, candidate in (('client', CLIENT), ('router', ROUTER), ('return', RETURN)):
        parent = tmp_path / ('fixture-' + name); parent.mkdir()
        folder, manifest = sample_artifact(parent, candidate)
        manifest['sourceCommit'] = 'a' * 40
        if name == 'return':
            returned = folder
            wrapper = bytearray((folder / 'forward.ota').read_bytes())
            struct.pack_into('<H', wrapper, 12, 65024)
            (folder / 'from-client.ota').write_bytes(wrapper)
            digest = hashlib.sha256(wrapper).hexdigest()
            manifest['artifacts']['from-client.ota'] = {'sha256': digest}
            (folder / 'CLIENT_RETURN.json').write_text(json.dumps({
                'sourceCommit': 'a' * 40, 'sha256': digest,
                'hardwareAcceptance': False, 'deploymentReady': False}))
        else:
            reports.append({'role': candidate['role'], 'sha256': manifest['artifacts']['forward.ota']['sha256']})
            folder = folder.rename(matrix / name)
        (folder / 'manifest.json').write_text(json.dumps(manifest))
    (matrix / 'ROLE_MATRIX.json').write_text(json.dumps({
        'sourceCommit': 'a' * 40, 'hostTests': 'passed', 'compiledBothRoles': True,
        'reproducedBothRoles': True, 'hardwareAcceptance': False, 'artifacts': reports}))
    return matrix, returned


def test_complete_bundle_seals_four_distinct_tuples_idempotently(tmp_path):
    matrix, returned = bundle(tmp_path)
    sealed, report = verify_bundle(matrix, returned, 'a' * 40, registry())
    assert len([e for line in sealed['lines'] for e in line['versions']]) == 4
    assert all(e['sha512'] for line in sealed['lines'] for e in line['versions'])
    assert not report['hardwareAcceptance']
    assert verify_bundle(matrix, returned, 'a' * 40, sealed)[0] == sealed


def test_nonpm_bundle_can_be_sealed_with_pm_without_crossing_board_identity(tmp_path):
    from bseed_nonpm_release import CANDIDATES
    from bseed_nonpm_variant_matrix import verify_bundle as verify_nonpm
    from test_bseed_nonpm_variant_matrix import bundle as nonpm_bundle
    matrix, returned = bundle(tmp_path)
    nonpm_root = tmp_path / 'nonpm-fixtures'; nonpm_root.mkdir()
    nonpm = nonpm_bundle(nonpm_root)
    original = registry()
    for candidate in CANDIDATES.values():
        original['lines'].append(dict(image_type=candidate['type'], board_key='o1jzcxou', versions=[
            dict(file_version=candidate['version'], version_str=candidate['build'], sha512=None)]))
    sealed, _ = verify_bundle(matrix, returned, 'a' * 40, original)
    verify_nonpm(nonpm, 'a' * 40)
    artifacts = [((nonpm / role / 'forward.ota').read_bytes(), c, c['type'])
                 for role, c in CANDIDATES.items()]
    sealed = seal_entries(sealed, artifacts, 'a' * 40)
    entries = [entry for line in sealed['lines'] for entry in line['versions']]
    assert len(entries) == 6 and all(entry['sha512'] for entry in entries)
    assert seal_entries(sealed, artifacts, 'a' * 40) == sealed
    wrapper = next(line for line in sealed['lines'] if line['image_type'] == 65024)['versions'][-1]
    assert wrapper['payload_role'] == 'Router' and wrapper['payload_image_type'] == 43556


def test_sealer_refuses_conflicting_payload_role_metadata():
    data = registry()
    data['lines'][0]['versions'][0]['payload_role'] = 'Router'
    before = copy.deepcopy(data)
    with pytest.raises(IdentityError, match='payload role metadata'):
        seal_entries(data, [(ota_image(CLIENT), CLIENT, CLIENT['type'])], 'a' * 40)
    assert data == before


@pytest.mark.parametrize('fault', ['not_reproduced', 'stale_source', 'wrong_board', 'dirty',
                                  'matrix_hash', 'wrapper_hash', 'return_report', 'native_bytes'])
def test_bundle_refuses_incomplete_or_inconsistent_evidence(tmp_path, fault):
    matrix, returned = bundle(tmp_path)
    path = matrix / 'ROLE_MATRIX.json'
    data = json.loads(path.read_text())
    if fault == 'not_reproduced': data['reproducedBothRoles'] = False
    if fault == 'stale_source': data['sourceCommit'] = 'b' * 40
    if fault == 'matrix_hash': data['artifacts'][0]['sha256'] = 'wrong'
    path.write_text(json.dumps(data))
    path = matrix / 'client' / 'manifest.json'
    data = json.loads(path.read_text())
    if fault == 'wrong_board': data['board'] = 'OUTLET_BSEED_TS011F'
    if fault == 'dirty': data['sourceDirty'] = True
    path.write_text(json.dumps(data))
    if fault == 'wrapper_hash':
        path = returned / 'manifest.json'; data = json.loads(path.read_text())
        data['artifacts']['from-client.ota']['sha256'] = 'wrong'
        path.write_text(json.dumps(data))
    if fault == 'return_report':
        path = returned / 'CLIENT_RETURN.json'; data = json.loads(path.read_text())
        data['deploymentReady'] = True
        path.write_text(json.dumps(data))
    if fault == 'native_bytes':
        path = matrix / 'client' / 'forward.ota'
        changed = bytearray(path.read_bytes()); changed[-5] ^= 1
        path.write_bytes(changed)
    original = registry(); before = copy.deepcopy(original)
    with pytest.raises(IdentityError):
        verify_bundle(matrix, returned, 'a' * 40, original)
    assert original == before
