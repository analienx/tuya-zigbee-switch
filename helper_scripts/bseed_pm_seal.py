"""Verify downloaded PM CI artifacts and optionally seal their immutable identities.

This checks local evidence, not GitHub run status or hardware. Independently
require both public workflows green at --source-commit before using --write.
No publication, OTA index changes or live device access.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from bseed_ota_identity import DEFAULT_REGISTRY, IdentityError, gate_image
from bseed_pm_release import CLIENT, ROUTER
from bseed_pm_variant_matrix import verify_artifact


def seal_entries(document, artifacts, source_commit):
    """All validation precedes mutation; preserve sealed bytes and old metadata."""
    result = copy.deepcopy(document)
    registry = {int(line['image_type']): line for line in result['lines']}
    for blob, candidate, image_type in artifacts:
        gate_image(blob, registry, candidate['build'], image_type, candidate['version'])
        line = registry[image_type]
        entries = [e for e in line['versions'] if int(e['file_version']) == candidate['version']]
        if len(entries) > 1:
            raise IdentityError('ambiguous reserved identity')
        entry = entries[0] if entries else None
        if entry is not None and entry.get('version_str') != candidate['build']:
            raise IdentityError('reserved build string differs')
        if entry is None:
            entry = {'file_version': candidate['version'], 'version_str': candidate['build']}
            line['versions'].append(entry)
        entry['sha512'] = hashlib.sha512(blob).hexdigest()
        for key, value in (('payload_role', candidate['role']), ('payload_image_type', candidate['type'])):
            if key in entry and entry[key] != value:
                raise IdentityError('sealed payload role metadata differs')
            entry[key] = value
        entry.setdefault('source_commit', source_commit)
        entry.setdefault('note', 'Consolidated BSEED CI candidate; hardware acceptance pending.')
    return result


def verify_bundle(matrix_dir, source_commit, document):
    if not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise IdentityError('source commit must be the full lowercase Git SHA')
    matrix_dir = Path(matrix_dir)
    matrix = json.loads((matrix_dir / 'ROLE_MATRIX.json').read_text())
    if (matrix.get('sourceCommit') != source_commit or matrix.get('hostTests') != 'passed'
            or matrix.get('compiledBothRoles') is not True
            or matrix.get('reproducedBothRoles') is not True
            or matrix.get('hardwareAcceptance') is not False):
        raise IdentityError('missing clean, reproduced role-matrix evidence')
    artifacts, report = [], []
    for role, candidate in (('router', ROUTER), ('client', CLIENT)):
        folder = matrix_dir / role
        verified = verify_artifact(folder, candidate, source_commit)
        matching = [a for a in matrix['artifacts'] if a['role'] == candidate['role']]
        if len(matching) != 1 or matching[0]['sha256'] != verified['sha256']:
            raise IdentityError('role matrix artifact hash mismatch')
        data = (folder / 'forward.ota').read_bytes()
        artifacts.append((data, candidate, candidate['type']))
        report.append(verified)
    sealed = seal_entries(document, artifacts, source_commit)
    return sealed, {'sourceCommit': source_commit, 'artifacts': report,
                    'hardwareAcceptance': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix-dir', required=True)
    p.add_argument('--nonpm-dir', required=True, help='verified non-PM role matrix from the same source')
    p.add_argument('--source-commit', required=True)
    p.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    p.add_argument('--write', action='store_true', help='seal only after independently confirming exact-SHA CI green')
    a = p.parse_args()
    original = a.registry.read_text(encoding='utf8')
    sealed, report = verify_bundle(a.matrix_dir, a.source_commit, json.loads(original))
    from bseed_nonpm_variant_matrix import verify_bundle as verify_nonpm_bundle
    from bseed_nonpm_release import CANDIDATES as NONPM
    report['nonpmArtifacts'] = verify_nonpm_bundle(a.nonpm_dir, a.source_commit)
    nonpm_artifacts = [((Path(a.nonpm_dir) / role / 'forward.ota').read_bytes(), candidate, candidate['type'])
                       for role, candidate in NONPM.items()]
    sealed = seal_entries(sealed, nonpm_artifacts, a.source_commit)
    if a.write:
        # Refuse racing changes; atomic replacement avoids a half-written registry.
        if a.registry.read_text(encoding='utf8') != original:
            raise IdentityError('registry changed during verification')
        temporary = a.registry.with_suffix('.json.sealing')
        with temporary.open('x', encoding='utf8', newline='\n') as f:
            f.write(json.dumps(sealed, separators=(',', ':')) + '\n')
        temporary.replace(a.registry)
    report['registryWritten'] = a.write
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
