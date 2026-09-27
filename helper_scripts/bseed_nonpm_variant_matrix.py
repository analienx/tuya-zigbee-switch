"""Public-CI-only native non-PM role builds, clean rebuilds and bundle verification."""
import argparse
import datetime as dt
import json
from pathlib import Path
import uuid

from bseed_nonpm_release import CANDIDATES
from bseed_ota_identity import IdentityError
from bseed_pm_variant_matrix import ROOT, run, verify_artifact


def verify_bundle(directory, source_commit):
    directory = Path(directory)
    matrix = json.loads((directory / 'ROLE_MATRIX.json').read_text())
    if (matrix.get('sourceCommit') != source_commit or matrix.get('compiledBothRoles') is not True
            or matrix.get('reproducedBothRoles') is not True or matrix.get('hardwareAcceptance') is not False):
        raise IdentityError('missing exact-source non-PM build/rebuild evidence')
    report = []
    for role, candidate in CANDIDATES.items():
        verified = verify_artifact(directory / role, candidate, source_commit)
        matches = [a for a in matrix.get('artifacts', []) if a.get('role') == candidate['role']]
        if len(matches) != 1 or matches[0]['sha256'] != verified['sha256']:
            raise IdentityError('non-PM matrix artifact hash mismatch')
        report.append(verified)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    output = (ROOT / (args.output_dir or 'build/bseed-nonpm-role-matrix-' +
        dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])).resolve()
    if not output.is_relative_to(ROOT / 'build') or output == ROOT / 'build' or output.exists():
        raise IdentityError('requires an unused path beneath build/')
    if run(['git', 'status', '--porcelain']).strip():
        raise IdentityError('native matrix requires clean tracked and untracked source')
    source = run(['git', 'rev-parse', 'HEAD']).strip()
    artifacts = []
    for role, candidate in CANDIDATES.items():
        originals = None
        for name in (role, 'repeat-' + role):
            folder = output / name
            command = (['bash', 'make_scripts/build_bseed_ts011f_nonpm_router.sh'] if role == 'router'
                       else ['bash', 'make_scripts/build_bseed_mains_client.sh', 'nonpm'])
            run([*command, str(folder)])
            verified = verify_artifact(folder, candidate, source)
            # Include transport wrappers and raw binary, not just the native OTA.
            blobs = {p.name: p.read_bytes() for p in folder.iterdir() if p.suffix in ('.bin', '.ota')}
            if originals is None:
                originals = blobs
                artifacts.append(verified)
            elif originals != blobs:
                raise IdentityError('non-reproducible non-PM role: ' + role)
    result = dict(sourceCommit=source, compiledBothRoles=True, reproducedBothRoles=True,
                  hardwareAcceptance=False, artifacts=artifacts)
    (output / 'ROLE_MATRIX.json').write_text(json.dumps(result, indent=2) + '\n')
    verify_bundle(output, source)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
