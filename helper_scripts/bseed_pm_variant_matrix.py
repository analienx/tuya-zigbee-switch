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
from bseed_pm_release import CLIENT, ROUTER, RELEASE_DATE, verify_native_image
from bseed_ota_identity import IdentityError

ROOT = Path(__file__).resolve().parents[1]
COMMON_TESTS = ('tests/test_unified_pm_v8.py', 'tests/test_pm_cluster_layout_guard.py',
                'tests/test_bseed_socket_antibrick.py',
                'tests/test_bseed_controlled_reboot.py',
                'tests/test_bseed_emergency_config.py',
                'tests/test_bseed_gate_optimization.py',
                'tests/test_bseed_basic_swbuild_limit.py',
                'tests/test_bseed_release_date.py',
                'tests/test_bseed_release_inputs.py',
                'tests/test_bseed_nonpm_variant_matrix.py',
                'tests/test_bseed_socket_version_policy.py',
                'tests/test_bseed_nonpm_transition.py',
                'tests/test_bseed_pm_telemetry_guard.py',
                'tests/test_bseed_client_ota_poll.py',
                'tests/test_bseed_poll_runtime.py',
                'tests/test_bseed_pm_native_image.py',
                'tests/test_bseed_pm_seal.py',
                'tests/test_pm_legacy_migration_quarantine.py',
                'tests/test_bseed_pm_client_return.py',
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

def run(command, *, env=None):
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        print(result.stdout[-6000:], file=sys.stderr, flush=True)
        raise RuntimeError(f'Verification failed ({result.returncode}): {command}')
    print('PASS', ' '.join(map(str, command)), flush=True)
    return result.stdout


def verify_artifact(path, expected, source_commit):
    def require(condition, reason):
        if not condition:
            raise IdentityError(reason)

    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
    require(manifest['sourceCommit'] == source_commit, 'build source mismatch')
    require(manifest['sourceDirty'] is False, 'dirty firmware build')
    require(manifest['board'] == expected.get('board', 'OUTLET_BSEED_PM_TS011F'), 'wrong board')
    require(manifest['canonicalConfig'] == expected.get('config', 'b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'), 'wrong pin map')
    require(manifest['swBuildId'] == expected['build'], 'wrong Basic build ID')
    require(manifest['buildDate'] == expected.get('date', RELEASE_DATE), 'wrong pinned release date')
    require(manifest['fileVersion'] == expected['version'], 'wrong file version')
    require(manifest['manufacturerCode'] == 4417, 'wrong manufacturer')
    image_type = manifest.get('imageType', manifest.get('clientImageType'))
    require(image_type == expected['type'], 'variant OTA identity mismatch')
    require(manifest['nvmMigrationsVersion'] >= 1, 'missing NVM schema')
    artifact = path / expected['artifact']
    data = artifact.read_bytes()
    require(10000 < len(data) < 0x80000, 'invalid PM image size')
    require(manifest['artifacts'][artifact.name]['sha256'] == hashlib.sha256(data).hexdigest(), 'manifest hash mismatch')
    require(manifest['otaHeader']['imageType'] == expected['type'], 'manifest header type mismatch')
    require(manifest['otaHeader']['fileVersion'] == expected['version'], 'manifest header version mismatch')
    require(manifest['otaHeader']['totalImageSize'] == len(data), 'manifest header size mismatch')
    integrity = verify_native_image(data, expected)
    return {'role': expected['role'], 'build': expected['build'],
            'imageType': image_type, 'sha256': hashlib.sha256(data).hexdigest(),
            'artifact': str(artifact), **integrity}

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
    router_env = dict(os.environ, BSEED_PM_CONSOLIDATED='1',
                      BSEED_PM_CONSOLIDATED_OUTPUT=str(output / 'router'))
    run(['bash', 'make_scripts/build_bseed_ts011f_pm_v8.sh'], env=router_env)
    result['artifacts'].append(verify_artifact(output / 'router', ROUTER, head))
    run(['bash', 'make_scripts/build_bseed_mains_client.sh', 'pm',
         str(output / 'client')])
    result['artifacts'].append(verify_artifact(output / 'client', CLIENT, head))
    if not (result['artifacts'][0]['sha256'] != result['artifacts'][1]['sha256']):
        raise AssertionError()
    # Two independent clean builds per role: manifests alone cannot establish
    # reproducibility, nor catch stale compiler outputs or role contamination.
    repeat_env = dict(os.environ, BSEED_PM_CONSOLIDATED='1',
                     BSEED_PM_CONSOLIDATED_OUTPUT=str(output / 'repeat-router'))
    run(['bash', 'make_scripts/build_bseed_ts011f_pm_v8.sh'], env=repeat_env)
    run(['bash', 'make_scripts/build_bseed_mains_client.sh', 'pm',
         str(output / 'repeat-client')])
    for name, candidate, original in zip(('router', 'client'), (ROUTER, CLIENT), result['artifacts']):
        repeated = verify_artifact(output / ('repeat-' + name), candidate, head)
        if repeated['sha256'] != original['sha256']:
            raise RuntimeError('non-reproducible clean build: ' + name)
    result['reproducedBothRoles'] = True
    result['compiledBothRoles'] = True
    (output / 'ROLE_MATRIX.json').write_text(json.dumps(result, indent=2)+'\n',
                                              encoding='utf8')
    print(json.dumps(result, indent=2)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
