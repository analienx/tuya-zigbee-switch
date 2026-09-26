"""Offline, fail-closed PM Router/Client source and binary verification matrix.

No device access, publication, OTA index changes, relay commands, or flashing.
Run from a Linux toolchain checkout with pytest and the Telink SDK installed.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMMON_TESTS = ('tests/test_unified_pm_v8.py', 'tests/test_pm_cluster_layout_guard.py',
                'tests/test_telink_pm_attribute_registration.py',
                'tests/test_bseed_pm_shared_contract.py',
                'tests/test_bseed_pm_variant_matrix.py',
                'tests/test_bseed_pm_zcl_read_probe.py',
                'tests/test_bseed_pm_role_audit.py', 'tests/test_bseed_pm_provision.py',
                'tests/test_bseed_pm_fleet_audit.py', 'tests/test_live_bseed_pm_metering.py',
                'tests/test_bseed_pm_ota_recovery_cross_role.py',
                'tests/test_bseed_ota_abort_forensics.py')
ROLE_TESTS = {'Router': ('tests/test_bseed_pm_v8_release.py',
                         'tests/test_bseed_golden_role_distribution.py'),
              'EndDevice': ('tests/test_bseed_mains_client.py',
                            'tests/test_bseed_mains_client_keepalive.py')}
ROUTER = {'role': 'Router', 'build': '1.2.5-bseedv8u5-rc5',
          'version': 0x12053012, 'type': 43556, 'artifact': 'forward.ota'}
CLIENT = {'role': 'EndDevice', 'build': '1.2.5-bseedcli10',
          'version': 0x12053012, 'type': 65024, 'artifact': 'forward.ota'}

def run(command, *, env=None):
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        print(result.stdout[-6000:], file=sys.stderr, flush=True)
        raise RuntimeError(f'Verification failed ({result.returncode}): {command}')
    print('PASS', ' '.join(map(str, command)), flush=True)
    return result.stdout


def verify_artifact(path, expected, source_commit):
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
    assert manifest['sourceCommit'] == source_commit, 'build source mismatch'
    assert manifest['sourceDirty'] is False, 'dirty firmware build'
    assert manifest['board'] == 'OUTLET_BSEED_PM_TS011F'
    assert manifest['canonicalConfig'] == 'b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'
    assert manifest['swBuildId'] == expected['build']
    assert manifest['fileVersion'] == expected['version']
    assert manifest['manufacturerCode'] == 4417
    image_type = manifest.get('imageType', manifest.get('clientImageType'))
    assert image_type == expected['type'], 'variant OTA identity mismatch'
    assert manifest['nvmMigrationsVersion'] >= 1
    artifact = path / expected['artifact']
    data = artifact.read_bytes()
    assert len(data) > 10000 and len(data) < 0x80000, 'invalid PM image size'
    assert manifest['artifacts'][artifact.name]['sha256'] == hashlib.sha256(data).hexdigest()
    assert manifest['otaHeader']['imageType'] == expected['type']
    assert manifest['otaHeader']['fileVersion'] == expected['version']
    assert manifest['otaHeader']['totalImageSize'] == len(data)
    return {'role': expected['role'], 'build': expected['build'],
            'imageType': image_type, 'sha256': hashlib.sha256(data).hexdigest(),
            'artifact': str(artifact)}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default=None,
                        help='Build-only artifacts, must be beneath ignored build/')
    parser.add_argument('--source-only', action='store_true',
                        help='Run host tests only; NOT firmware or hardware acceptance')
    args = parser.parse_args(argv)
    root_build = (ROOT / 'build').resolve()
    destination = args.output_dir or ('build/bseed-pm-role-matrix-' +
        dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') +
        '-' + uuid.uuid4().hex[:8])
    output = (ROOT / destination).resolve()
    if not output.is_relative_to(root_build) or output == root_build:
        parser.error('Output must be under the ignored build directory')
    if output.exists():
        parser.error('Refusing to overwrite previous build/matrix evidence')
    head = run(['git', 'rev-parse', 'HEAD']).strip()
    run(['make', 'stub/build'])
    run(['make', 'stub/build_end_device'])
    tests = list(dict.fromkeys((*COMMON_TESTS, *ROLE_TESTS['Router'],
                                *ROLE_TESTS['EndDevice'])))
    run([sys.executable, '-m', 'pytest', *tests, '-q'])
    result = {'outputDir': str(output), 'sourceCommit': head, 'roles': ['Router', 'EndDevice'],
              'hostTests': 'passed', 'compiledBothRoles': False,
              'hardwareAcceptance': False, 'artifacts': []}
    if args.source_only:
        print(json.dumps(result, indent=2)); return 0
    if run(['git', 'status', '--porcelain']).strip():
        raise RuntimeError('Build matrix requires clean tracked and untracked sources')
    router_env = dict(os.environ, BSEED_PM_ROUTER_READ_FIX='1',
                      BSEED_PM_ROUTER_READ_FIX_OUTPUT=str(output / 'router'))
    run(['bash', 'make_scripts/build_bseed_ts011f_pm_v8.sh'], env=router_env)
    result['artifacts'].append(verify_artifact(output / 'router', ROUTER, head))
    run(['bash', 'make_scripts/build_bseed_mains_client.sh', 'pm',
         str(output / 'client')])
    result['artifacts'].append(verify_artifact(output / 'client', CLIENT, head))
    assert result['artifacts'][0]['sha256'] != result['artifacts'][1]['sha256']
    result['compiledBothRoles'] = True
    (output / 'ROLE_MATRIX.json').write_text(json.dumps(result, indent=2)+'\n',
                                              encoding='utf8')
    print(json.dumps(result, indent=2)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
