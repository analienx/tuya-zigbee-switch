"""Verify exact-SHA public Actions and prepare a sealed registry proposal on CI.

No GitHub writes, releases, index changes or hardware operations.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

REQUIRED = ('test', 'BSEED PM role matrix', 'BSEED non-PM role matrix',
            'Telink Router TC32 validation', 'BSEED experimental mains clients',
            'BSEED Router hardening evidence', 'BSEED shared hardening')


def api_request(url, token, binary=False):
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json'})
    # Artifact downloads redirect to signed storage URLs. Authenticate only the
    # initial API request; forwarding a bearer header breaks storage auth.
    req.add_unredirected_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as response:
        data = response.read()
    return data if binary else json.loads(data)


def package_candidates(report, root):
    """Package only already-verified native payloads; never include FORCE wrappers."""
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Candidate packaging runs only on GitHub-hosted Actions')
    labels = {43556: 'pm-router', 65024: 'pm-client',
              43555: 'nonpm-router', 65026: 'nonpm-client'}
    rows = [*report['artifacts'], *report['nonpmArtifacts']]
    if sorted(row['imageType'] for row in rows) != sorted(labels):
        raise ValueError('Local-flashing package must contain exactly four socket variants')
    package = root / 'flash-candidates'
    package.mkdir(exist_ok=False)
    entries = []
    for row in rows:
        if not row['nativeIntegrityVerified'] or not row['basicBuildIdVerified']:
            raise ValueError('Candidate lacks native integrity evidence')
        source = Path(row['artifact'])
        ota = source.read_bytes()
        if hashlib.sha256(ota).hexdigest() != row['sha256']:
            raise ValueError('Verified artifact changed before packaging')
        # The sealer has checked the 56-byte OTA header and type-0 subelement,
        # native length/version/startup marker/CRC and length-prefixed build ID.
        raw = ota[62:]
        original_bin = source.with_suffix('.bin')
        if original_bin.exists() and original_bin.read_bytes() != raw:
            raise ValueError('Native BIN and verified OTA payload differ')
        folder = package / labels[row['imageType']]
        folder.mkdir()
        (folder / 'firmware.ota').write_bytes(ota)
        (folder / 'firmware.bin').write_bytes(raw)
        shutil.copyfile(source.with_name('manifest.json'), folder / 'manifest.json')
        entries.append({
            'variant': folder.name, 'role': row['role'], 'build': row['build'],
            'imageType': row['imageType'], 'manufacturerCode': 4417,
            'fileVersion': int.from_bytes(ota[14:18], 'little'),
            'nativeBytes': len(raw), 'otaBytes': len(ota),
            'nativeSha256': hashlib.sha256(raw).hexdigest(),
            'nativeSha512': hashlib.sha512(raw).hexdigest(),
            'otaSha256': row['sha256'], 'otaSha512': hashlib.sha512(ota).hexdigest(),
        })
    metadata = {'sourceCommit': report['sourceCommit'], 'entries': entries,
                'offlineIntegrityVerified': True, 'hardwareAcceptance': False,
                'publicActions': report['publicActions']}
    (package / 'CANDIDATE_PACKAGE.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (package / 'README.md').write_text(
        '# Analienx BSEED firmware (based on Romasku) — CI-verified socket firmware\n\n'
        'Four independently identified socket variants are included. Select the exact '
        'PM/non-PM board and Router/Client role. firmware.bin is the verified native '
        'application image, not a complete flash/NVM backup. firmware.ota is the normal '
        'role-specific OTA image; no stock/FORCE/role-transition wrapper is included.\n\n'
        'The accompanying JSON records runner-produced hashes and exact-SHA CI provenance. '
        'These bytes passed native integrity, identity and reproducibility checks on '
        'GitHub-hosted Actions. No local build is required. Do not use make flash, '
        'which would rebuild locally.\n\n'
        'Use the existing board-specific hardware/profile tooling with these downloaded '
        'files. Implementation details and diagnostic layouts are documented in '
        'docs/bseed_router_hardening_20261003.md. This workflow prepares files; '
        'it does not flash a device or measure its on-device behavior.\n')
    return metadata


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Finalization runs only on GitHub-hosted Actions')
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['EXPECTED_HEAD']
    token = os.environ['GH_TOKEN']
    root = Path('build/ci-finalization')
    root.mkdir(parents=True, exist_ok=True)

    def request(path, binary=False):
        url = 'https://api.github.com/repos/' + repo + '/' + path
        return api_request(url, token, binary)

    deadline = time.monotonic() + 2400
    while True:
        runs = request('actions/runs?head_sha=' + sha + '&per_page=100')['workflow_runs']
        selected = {}
        for name in REQUIRED:
            matching = [r for r in runs if r['name'] == name and r['head_sha'] == sha
                        and r['event'] == 'pull_request']
            if matching:
                selected[name] = max(matching, key=lambda r: r['id'])
        failed = [name for name, run in selected.items()
                  if run['status'] == 'completed' and run['conclusion'] != 'success']
        if failed:
            raise RuntimeError('Exact-SHA prerequisites failed: ' + ', '.join(failed))
        if len(selected) == len(REQUIRED) and all(r['conclusion'] == 'success' for r in selected.values()):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Exact-SHA prerequisites did not finish')
        print('Waiting for exact-SHA public CI prerequisites', flush=True)
        time.sleep(30)

    def download(workflow, name, target):
        run = selected[workflow]
        artifacts = request('actions/runs/' + str(run['id']) + '/artifacts?per_page=100')['artifacts']
        matches = [a for a in artifacts if a['name'] == name and not a['expired']]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous artifact: ' + name)
        raw = request('actions/artifacts/' + str(matches[0]['id']) + '/zip', binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for entry in archive.infolist():
                if not (target / entry.filename).resolve().is_relative_to(target.resolve()):
                    raise ValueError('Artifact path escapes destination')
            archive.extractall(target)
        return target

    pm = download('BSEED PM role matrix', 'bseed-pm-role-matrix-' + sha, root / 'pm')
    nonpm = download('BSEED non-PM role matrix', 'bseed-nonpm-role-matrix-' + sha, root / 'nonpm')
    # upload-artifact may preserve the fixed directory preceding a wildcard.
    def containing(folder, filename):
        matches = list(folder.rglob(filename))
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous ' + filename)
        return matches[0].parent
    registry = root / 'bseed_identity.json'
    shutil.copyfile('zigbee2mqtt/ota/bseed_identity.json', registry)
    command = [sys.executable, 'helper_scripts/bseed_pm_seal.py',
               '--matrix-dir', str(containing(pm, 'ROLE_MATRIX.json')),
               '--nonpm-dir', str(containing(nonpm, 'ROLE_MATRIX.json')),
               '--source-commit', sha, '--registry', str(registry), '--write']
    report = json.loads(subprocess.check_output(command, text=True))
    report['publicActions'] = {name: r['html_url'] for name, r in selected.items()}
    report['registryProposalSha256'] = hashlib.sha256(registry.read_bytes()).hexdigest()
    report['registryCommitted'] = False
    report['candidatePackage'] = package_candidates(report, root)
    (root / 'CI_SEAL_REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))
    # Reviewable text counterpart to the artifact. A supervisor can commit
    # these exact runner-produced bytes without running the sealer locally.
    print('BEGIN_VERIFIED_REGISTRY')
    print(registry.read_text(encoding='utf8'), end='')
    print('END_VERIFIED_REGISTRY')


if __name__ == '__main__':
    main()
