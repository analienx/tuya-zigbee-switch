"""Validate both non-PM native artifacts and reject inconsistent CI evidence."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_nonpm_release import CANDIDATES
from bseed_nonpm_variant_matrix import verify_bundle
from bseed_ota_identity import IdentityError
from test_bseed_pm_variant_matrix import sample_artifact


def bundle(tmp_path):
    directory = tmp_path / 'nonpm'; directory.mkdir()
    reports = []
    for role, candidate in CANDIDATES.items():
        root = tmp_path / ('fixture-' + role); root.mkdir()
        folder, manifest = sample_artifact(root, candidate)
        manifest['sourceCommit'] = 'a' * 40
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        folder.rename(directory / role)
        reports.append(dict(role=candidate['role'], sha256=manifest['artifacts']['forward.ota']['sha256']))
    (directory / 'ROLE_MATRIX.json').write_text(json.dumps(dict(
        sourceCommit='a' * 40, compiledBothRoles=True, reproducedBothRoles=True,
        hardwareAcceptance=False, artifacts=reports)))
    return directory


def test_both_nonpm_roles_verified(tmp_path):
    reports = verify_bundle(bundle(tmp_path), 'a' * 40)
    assert {r['role'] for r in reports} == {'Router', 'EndDevice'}


@pytest.mark.parametrize('fault', ['source', 'rebuild', 'hardware_claim', 'duplicate_role',
                                  'hash', 'board', 'date', 'dirty', 'bytes'])
def test_reject_incomplete_or_inconsistent_nonpm_evidence(tmp_path, fault):
    folder = bundle(tmp_path)
    path = folder / 'ROLE_MATRIX.json'
    data = json.loads(path.read_text())
    if fault == 'source': data['sourceCommit'] = 'b' * 40
    if fault == 'rebuild': data['reproducedBothRoles'] = False
    if fault == 'hardware_claim': data['hardwareAcceptance'] = True
    if fault == 'duplicate_role': data['artifacts'].append(data['artifacts'][0])
    if fault == 'hash': data['artifacts'][0]['sha256'] = 'wrong'
    path.write_text(json.dumps(data))
    path = folder / 'client' / 'manifest.json'
    data = json.loads(path.read_text())
    if fault == 'board': data['board'] = 'OUTLET_BSEED_PM_TS011F'
    if fault == 'date': data['buildDate'] = '20990101'
    if fault == 'dirty': data['sourceDirty'] = True
    path.write_text(json.dumps(data))
    if fault == 'bytes':
        path = folder / 'client' / 'forward.ota'
        content = bytearray(path.read_bytes()); content[-8] ^= 1
        path.write_bytes(content)
    with pytest.raises(IdentityError): verify_bundle(folder, 'a' * 40)
