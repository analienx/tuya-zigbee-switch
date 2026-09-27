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
            'Telink Router TC32 validation', 'BSEED experimental mains clients')


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Finalization runs only on GitHub-hosted Actions')
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['EXPECTED_HEAD']
    token = os.environ['GH_TOKEN']
    root = Path('build/ci-finalization')
    root.mkdir(parents=True, exist_ok=True)

    def request(path, binary=False):
        url = 'https://api.github.com/repos/' + repo + '/' + path
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
                                                   'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
        return data if binary else json.loads(data)

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
    ret = download('BSEED PM role matrix', 'bseed-pm-client-return-experimental-' + sha, root / 'return')
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
               '--return-dir', str(containing(ret, 'CLIENT_RETURN.json')),
               '--source-commit', sha, '--registry', str(registry), '--write']
    report = json.loads(subprocess.check_output(command, text=True))
    report['publicActions'] = {name: r['html_url'] for name, r in selected.items()}
    report['registryProposalSha256'] = hashlib.sha256(registry.read_bytes()).hexdigest()
    report['registryCommitted'] = False
    (root / 'CI_SEAL_REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))
    # Reviewable text counterpart to the artifact. A supervisor can commit
    # these exact runner-produced bytes without running the sealer locally.
    print('BEGIN_VERIFIED_REGISTRY')
    print(registry.read_text(encoding='utf8'), end='')
    print('END_VERIFIED_REGISTRY')


if __name__ == '__main__':
    main()
