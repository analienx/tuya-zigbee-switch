"""Profile-driven BSEED OTA maintenance entry point; no implicit flash/retry.

Use a PRIVATE profile outside git. `flash` requires exact IEEE typed a second time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('device', 'ieee', 'manufacturer', 'model', 'preflash_role',
            'image', 'sha256', 'url', 'mqtt_config', 'broker', 'workdir',
            'manufacturer_code', 'image_type', 'file_version', 'expect_relay',
            'index_url', 'index_output', 'template_index', 'postflash_role', 'postflash_build')


def load_profile(path):
    source = Path(path).expanduser().resolve()
    if source.is_relative_to(ROOT):
        raise ValueError('Private OTA profile must be outside the repository')
    data = json.loads(source.read_text(encoding='utf8'))
    missing = [key for key in REQUIRED if not data.get(key) and data.get(key) != 0]
    if missing: raise ValueError('Missing profile fields: ' + ', '.join(missing))
    if not str(data['ieee']).startswith('0x') or len(data['ieee']) != 18:
        raise ValueError('Expected full 0x-prefixed IEEE address')
    if data['postflash_role'] not in ('EndDevice', 'Router'):
        raise ValueError('Unexpected postflash role')
    for key in ('image', 'mqtt_config', 'workdir', 'index_output', 'template_index'):
        data[key] = str(Path(data[key]).expanduser().resolve())
    if Path(data['index_output']).resolve().is_relative_to(ROOT):
        raise ValueError('Private OTA index must not be inside repository')
    if Path(data['workdir']).resolve().is_relative_to(ROOT):
        raise ValueError('Private logs and OTA lock must not be inside repository')
    if data.get('non_pm') is True:
        if data.get('require_pm') is not False or data.get('preflash_build') is None or not data.get('preflash_relay_physical_mode'):
            raise ValueError('Non-PM requires explicit require_pm=false, preflash_build and preflash_relay_physical_mode')
    return data


def make_index(profile):
    image = Path(profile['image']).read_bytes()
    if hashlib.sha256(image).hexdigest() != profile['sha256']:
        raise ValueError('Image SHA256 mismatch; no OTA index generated')
    candidates = json.loads(Path(profile['template_index']).read_text(encoding='utf8'))
    if not isinstance(candidates, list): raise ValueError('Template index must be JSON list')
    matches = [item for item in candidates if item.get('manufacturerCode') == int(profile['manufacturer_code'])
               and item.get('imageType') == int(profile['image_type'])
               and profile['manufacturer'] in item.get('manufacturerName', [])]
    if len(matches) != 1: raise ValueError('Must identify exactly one matching template index entry')
    from bseed_targeted_z2m_ota import verify_image
    from types import SimpleNamespace
    image_args = SimpleNamespace(image=profile['image'], native_image=profile.get('native_image'),
                sha256=profile['sha256'], url=profile['url'],
                manufacturer_code=int(profile['manufacturer_code']), image_type=int(profile['image_type']),
                file_version=int(str(profile['file_version']), 0), non_pm=profile.get('non_pm') is True)
    _, header = verify_image(image_args)
    entry = dict(matches[0])
    if entry.get('fileVersion') != image_args.file_version:
        raise ValueError('Template entry version differs from signed-off OTA header')
    entry.update(fileName=Path(profile['image']).name, fileSize=len(image),
                 url=profile['url'], sha512=hashlib.sha512(image).hexdigest())
    destination = Path(profile['index_output'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps([entry], indent=2) + '\n'
    if destination.exists() and destination.read_text(encoding='utf8') != content:
        raise ValueError('Refusing to overwrite a different private index: ' + str(destination))
    destination.write_text(content, encoding='utf8')
    return {'file': str(destination), 'image_sha256': profile['sha256'],
            'image_size': len(image), 'manufacturer_code': header[4], 'image_type': header[5]}


def runner_args(profile, mode, *, confirm_unloaded=False, accept_risk=False):
    keys = [('device','device'), ('ieee','ieee'), ('manufacturer','manufacturer'),
            ('model','model'), ('preflash_role','role'), ('image','image'),
            ('sha256','sha256'), ('url','url'), ('mqtt_config','mqtt-config'),
            ('broker','broker'), ('workdir','workdir'),
            ('manufacturer_code','manufacturer-code'), ('image_type','image-type'),
            ('file_version','file-version'), ('expect_relay','expect-relay'),
            ('index_url','index-url')]
    cmd = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_targeted_z2m_ota.py'),
           '--mode', mode]
    for key, flag in keys: cmd.extend(['--' + flag, str(profile[key])])
    if profile.get('native_image'):
        cmd.extend(['--native-image', str(profile['native_image'])])
    if profile.get('relay_get_key'):
        cmd.extend(['--relay-get-key', profile['relay_get_key']])
    if profile.get('non_pm') is True:
        cmd.append('--non-pm')
        if mode == 'flash':
            cmd.extend(['--hardware-evidence', str(profile.get('recovery_evidence', ''))])
            if confirm_unloaded: cmd.append('--confirm-load-unplugged')
            if accept_risk: cmd.append('--accept-nonrecoverable-ota-risk')
        for key, flag in [('preflash_build','preflash-build'),('preflash_relay_physical_mode','preflash-relay-physical-mode')]:
            if key not in profile: raise ValueError('Non-PM link gate requires '+key)
            cmd.extend(['--'+flag,str(profile[key])])
    for key, flag in [('block_bytes','max-block-bytes'), ('check_timeout_seconds','check-timeout-seconds'),
                       ('monitor_seconds','timeout-seconds')]:
        if key in profile: cmd.extend(['--' + flag, str(profile[key])])
    return cmd


def rejoin_cmd(profile, confirmation, evidence_path):
    if profile['preflash_role'] == profile['postflash_role']:
        raise ValueError('Scoped rejoin is only for an explicit cross-role OTA')
    if not profile.get('join_via'):
        raise ValueError('Cross-role rejoin requires verified scoped Router name: join_via')
    return [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_z2m_rejoin_window.py'),
            '--target', profile['device'], '--ieee', profile['ieee'],
            '--confirm-ieee', confirmation, '--permit-via', profile['join_via'],
            '--seconds', str(profile.get('join_seconds', 120)),
            '--expect-build', profile['postflash_build'], '--expect-role', profile['postflash_role'],
            '--mqtt-config', profile['mqtt_config'], '--broker', profile['broker'],
            '--campaign-lock', str(Path(profile['workdir'])/'ACTIVE_LOCK.json'),
            '--output', str(evidence_path)]


def metadata_cmd(profile, confirmation, evidence):
    return [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_z2m_metadata_refresh.py'),
            '--device', profile['device'], '--ieee', profile['ieee'], '--confirm-ieee', confirmation,
            '--expect-role', profile['postflash_role'], '--expect-build', profile['postflash_build'],
            '--mqtt-config', profile['mqtt_config'], '--broker', profile['broker'], '--output', str(evidence)]


def postflash_cmd(profile, evidence):
    cmd = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_z2m_postflash_verify.py'),
            '--device', profile['device'], '--ieee', profile['ieee'],
            '--expect-role', profile['postflash_role'], '--expect-build', profile['postflash_build'],
            '--mqtt-config', profile['mqtt_config'], '--broker', profile['broker'],
            '--output', str(evidence), '--observe-seconds', str(profile.get('observe_seconds', 20))]
    if profile.get('require_pm'): cmd.append('--require-pm')
    if profile['preflash_role']==profile['postflash_role']:
        cmd.extend(['--preflash-lock',str(Path(profile['workdir'])/'ACTIVE_LOCK.json'),
                    '--expected-image-sha256',str(profile['sha256']),
                    '--relay-get-key',str(profile.get('relay_get_key','state'))])
    return cmd




def reinterview_cmd(profile, confirmation, evidence):
    """Only after confirmed same-role OTA OK, never after an ABORT or role transition."""
    if profile['preflash_role'] != profile['postflash_role']:
        raise ValueError('Cross-role OTA follows scoped rejoin and metadata workflow')
    if confirmation != profile['ieee']:
        raise ValueError('Confirm exact IEEE for target-only post-OTA interview')
    cmd=[sys.executable,'-u',str(ROOT/'helper_scripts/bseed_z2m_postota_reinterview.py')]
    for key,flag in [('device','device'),('ieee','ieee'),('manufacturer','manufacturer'),
                     ('model','model'),('postflash_role','expect-role'),('postflash_build','expect-build'),
                     ('mqtt_config','mqtt-config'),('broker','broker')]:
        cmd.extend(['--'+flag,str(profile[key])])
    for key,flag in [('postflash_manufacturer','manufacturer'),('postflash_model','model')]:
        if profile.get(key):
            cmd[cmd.index('--'+flag)+1]=str(profile[key])
    cmd.extend(['--image-sha256',str(profile['sha256'])])
    cmd.extend(['--confirm-ieee',confirmation,'--campaign-lock',
                str(Path(profile['workdir'])/'ACTIVE_LOCK.json'),'--output',str(evidence),
                '--settle-seconds',str(profile.get('postota_settle_seconds',10))])
    return cmd


def provision_cmd(profile, confirmation, evidence):
    """Explicit per-device PM repair; returns a command, never runs implicitly on status."""
    if not profile.get('require_pm'): raise ValueError('Not a PM campaign')
    if profile['postflash_role'] != 'EndDevice':
        raise ValueError('Client-only PM provision: Router requires separate verified configuration')
    if confirmation != profile['ieee']: raise ValueError('Confirm exact IEEE to provision PM')
    if not profile.get('pm_ssh_host') or not profile.get('pm_ssh_key'):
        raise ValueError('PM campaign requires private pm_ssh_host and pm_ssh_key')
    cmd = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_pm_provision.py'),
           '--device', profile['device'], '--ieee', profile['ieee'],
           '--confirm-ieee', confirmation, '--expect-role', profile['postflash_role'],
           '--expect-build', profile['postflash_build'], '--mqtt-config', profile['mqtt_config'],
           '--broker', profile['broker'], '--ssh-host', profile['pm_ssh_host'],
           '--ssh-key', profile['pm_ssh_key'], '--output', str(evidence),
           '--max-writes', str(profile.get('pm_max_writes', 4)),
           '--observe-seconds', str(profile.get('pm_observe_seconds', 135)), '--apply']
    if profile.get('pm_allow_configure'): cmd.append('--allow-configure')
    if profile.get('pm_expected_idle'): cmd.append('--expect-idle')
    return cmd


def role_audit_cmd(profile, evidence):
    if not profile.get('require_pm'): raise ValueError('PM role audit requires a PM profile')
    if not profile.get('pm_ssh_host') or not profile.get('pm_ssh_key'):
        raise ValueError('Private PM SSH settings required')
    cmd=[sys.executable,'-u',str(ROOT/'helper_scripts/bseed_pm_role_audit.py'),
         '--device',profile['device'],'--ieee',profile['ieee'],
         '--expect-role',profile['postflash_role'],'--expect-build',profile['postflash_build'],
         '--mqtt-config',profile['mqtt_config'],'--broker',profile['broker'],
         '--ssh-host',profile['pm_ssh_host'],'--ssh-key',profile['pm_ssh_key'],
         '--observe-seconds',str(profile.get('pm_audit_seconds',85)),'--output',str(evidence)]
    if profile.get('pm_settings_baseline'):cmd.extend(['--baseline',profile['pm_settings_baseline']])
    return cmd



def verified_router_pm_candidate(profile):
    """Allow only one same-role PM Router candidate proven by the cross-role binary matrix."""
    if profile.get('preflash_role') != 'Router' or profile.get('postflash_role') != 'Router':
        raise ValueError('Router PM candidate may not change Zigbee role')
    path = profile.get('pm_router_matrix_evidence')
    if not path or not Path(path).is_file():
        raise ValueError('Router PM flash requires private cross-role build-matrix evidence')
    matrix = json.loads(Path(path).read_text(encoding='utf8'))
    if matrix.get('hardwareAcceptance') is not False or matrix.get('compiledBothRoles') is not True:
        raise ValueError('Missing validated build-only Router+Client matrix')
    roles = matrix.get('artifacts', [])
    if len(roles) != 2 or {x.get('role') for x in roles} != {'Router', 'EndDevice'}:
        raise ValueError('Both PM roles must appear in the matrix')
    router = next(x for x in roles if x['role'] == 'Router')
    if (router.get('sha256'), router.get('imageType'), router.get('build')) != (
            profile['sha256'], int(profile['image_type']), profile['postflash_build']):
        raise ValueError('Private Router candidate differs from validated matrix')
    if profile.get('relay_get_key') != 'state_relay' or not profile.get('require_pm'):
        raise ValueError('Router PM flash requires a verified relay endpoint and PM gate')
    for key in ('pm_ssh_host','pm_ssh_key','pm_settings_baseline'):
        if not profile.get(key): raise ValueError('Router PM flash missing '+key)
    if not Path(profile['pm_ssh_key']).is_file() or not Path(profile['pm_settings_baseline']).is_file():
        raise ValueError('Router PM preflash key/settings baseline is missing')
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='Private JSON profile outside git')
    parser.add_argument('--mode', choices=['prepare', 'preflight', 'link-gate', 'qualify', 'check', 'flash', 'transition', 'rejoin', 'metadata', 'provision-pm', 'audit-pm', 'postflash', 'reinterview', 'status'], required=True)
    parser.add_argument('--confirm-ieee', help='Required for flash, transition, rejoin, metadata and provision-pm; must match profile IEEE exactly')
    parser.add_argument('--accept-nonrecoverable-ota-risk', action='store_true', help='One-canary OTA may permanently fail; no physical readback/recovery is available')
    parser.add_argument('--confirm-load-unplugged', action='store_true', help='Non-PM flash only: operator just verified no physical appliance is connected')
    args = parser.parse_args()
    profile = load_profile(args.profile)
    if args.accept_nonrecoverable_ota_risk and not (args.mode == 'flash' and profile.get('non_pm') is True):
        raise SystemExit('Risk flag only allowed for exact non-PM flash')
    if args.confirm_load_unplugged and not (args.mode == 'flash' and profile.get('non_pm') is True):
        raise SystemExit('--confirm-load-unplugged is accepted only for explicitly targeted non-PM flash')
    if profile.get('require_pm') and profile['postflash_role'] == 'Router' and args.mode in ('flash','transition','rejoin'):
        if args.mode != 'flash': raise SystemExit('Router PM role transition and auto-provisioning not validated')
        verified_router_pm_candidate(profile)  # no Router provisioning: flash only, then audit separately
    work = Path(profile['workdir'])
    if args.mode == 'qualify':
        if args.confirm_ieee or profile.get('non_pm') is not True or profile.get('require_pm') is not False or profile['preflash_role'] != 'EndDevice':
            raise SystemExit('Read-only qualification requires non-PM EndDevice profile and no flash confirmation')
        from bseed_targeted_z2m_ota import archive_prior_check
        work.mkdir(parents=True, exist_ok=True)
        archive_prior_check(work)  # Never reuse an earlier OTA check if this new sequence fails.
        gate = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_nonpm_link_gate.py'),
                '--profile', args.profile]
        for stage, command in [('link_gate_before', gate), ('ota_check', runner_args(profile, 'check')),
                               ('link_gate_after', gate)]:
            status = subprocess.call(command)
            if status:
                print('QUALIFICATION_STOPPED_NO_FLASH', stage, 'exit', status, flush=True)
                raise SystemExit(status)
        print('QUALIFICATION_PASSED_NO_FLASH', profile['device'], flush=True)
        return
    if args.mode == 'prepare':
        print(json.dumps(make_index(profile), indent=2))
        return
    if args.mode == 'link-gate':
        if profile.get('non_pm') is not True or profile['preflash_role'] != 'EndDevice':
            raise SystemExit('Link gate is only for an explicit non-PM EndDevice campaign')
        cmd = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_nonpm_link_gate.py'),
               '--profile', args.profile]
        raise SystemExit(subprocess.call(cmd))
    if args.mode == 'status':
        for filename in ('ACTIVE_LOCK.json', 'LAST_CHECK.json'):
            f = work / filename
            print(filename, f.read_text(encoding='utf8') if f.exists() else 'not present')
        return
    if args.mode == 'audit-pm':
        import uuid
        evidence = work / ('pm_role_audit_' + uuid.uuid4().hex + '.json')
        raise SystemExit(subprocess.call(role_audit_cmd(profile, evidence)))
    if args.mode == 'reinterview':
        if args.confirm_ieee != profile['ieee']:
            raise SystemExit('Target re-interview refused: confirm exact IEEE')
        import uuid
        evidence = work / ('postota_interview_' + uuid.uuid4().hex + '.json')
        print('PRIVATE_EVIDENCE', evidence, flush=True)
        raise SystemExit(subprocess.call(reinterview_cmd(profile, args.confirm_ieee, evidence)))
    if args.mode == 'provision-pm':
        if args.confirm_ieee != profile['ieee']:
            raise SystemExit('Provision refused: confirm exact IEEE')
        import uuid
        evidence = work / ('pm_provision_' + uuid.uuid4().hex + '.json')
        raise SystemExit(subprocess.call(provision_cmd(profile, args.confirm_ieee, evidence)))
    if args.mode == 'postflash':
        import uuid
        evidence = work / ('postflash_' + uuid.uuid4().hex + '.json')
        cmd = postflash_cmd(profile, evidence)
        print('PRIVATE_EVIDENCE', evidence, flush=True)
        raise SystemExit(subprocess.call(cmd))
    if args.mode in ('transition', 'rejoin', 'metadata'):
        if args.confirm_ieee != profile['ieee']:
            raise SystemExit('Transition/rejoin/metadata refused: confirm exact target IEEE')
        if profile['preflash_role'] == profile['postflash_role']:
            raise SystemExit('Use normal flash for same-role firmware, not transition/rejoin/metadata')
        import uuid
        if args.mode == 'metadata':
            evidence = work / ('metadata_' + uuid.uuid4().hex + '.json')
            raise SystemExit(subprocess.call(metadata_cmd(profile, args.confirm_ieee, evidence)))
        # Resolve the scoped recovery route *before* submitting a firmware image.
        planned_join = rejoin_cmd(profile, args.confirm_ieee, work / ('rejoin_' + uuid.uuid4().hex + '.json'))
        if profile.get('require_pm'): provision_cmd(profile, args.confirm_ieee, work / 'pm_prevalidated.json')
        if args.mode == 'transition':
            print('ONE_DEVICE_ROLE_TRANSITION', profile['ieee'], flush=True)
            flashed = subprocess.call(runner_args(profile, 'flash'))
            if flashed: raise SystemExit(flashed)  # no automatic retry after failure
        join_evidence = work / ('rejoin_' + uuid.uuid4().hex + '.json')
        joined = subprocess.call(rejoin_cmd(profile, args.confirm_ieee, join_evidence))
        if joined: raise SystemExit(joined)
        metadata_evidence = work / ('metadata_' + uuid.uuid4().hex + '.json')
        refreshed = subprocess.call(metadata_cmd(profile, args.confirm_ieee, metadata_evidence))
        if refreshed: raise SystemExit(refreshed)
        if profile.get('require_pm'):
            pm_evidence = work / ('pm_provision_' + uuid.uuid4().hex + '.json')
            provisioned = subprocess.call(provision_cmd(profile, args.confirm_ieee, pm_evidence))
            if provisioned: raise SystemExit(provisioned)
        if args.mode == 'transition':
            post_evidence = work / ('postflash_' + uuid.uuid4().hex + '.json')
            raise SystemExit(subprocess.call(postflash_cmd(profile, post_evidence)))
        return
    if args.mode == 'flash':
        if args.confirm_ieee != profile['ieee']:
            raise SystemExit('Flash refused: supply --confirm-ieee with the exact target IEEE')
        if profile['preflash_role'] != profile['postflash_role']:
            raise SystemExit('Cross-role flash refused: use --mode transition for scoped rejoin')
        if profile.get('non_pm') is True:
            from bseed_nonpm_recovery_gate import verify_recovery
            verify_recovery(profile, confirm_unloaded=args.confirm_load_unplugged,
                            accept_nonrecoverable_ota=args.accept_nonrecoverable_ota_risk)
        elif args.confirm_load_unplugged:
            raise SystemExit('Load confirmation flag is only valid for non-PM flash')
        if profile.get('require_pm') and profile['postflash_role'] == 'EndDevice':
            provision_cmd(profile, args.confirm_ieee, work / 'pm_prevalidated.json')
        print('EXPLICIT_FLASH_TARGET', profile['ieee'], profile['device'], flush=True)
        flashed = subprocess.call(runner_args(profile, 'flash', confirm_unloaded=args.confirm_load_unplugged, accept_risk=args.accept_nonrecoverable_ota_risk))
        if flashed: raise SystemExit(flashed)  # Never interview or retry after OTA failure.
        import uuid
        interview = work / ('postota_interview_' + uuid.uuid4().hex + '.json')
        interviewed = subprocess.call(reinterview_cmd(profile, args.confirm_ieee, interview))
        if interviewed:
            print('POSTOTA_INTERVIEW_UNCONFIRMED', interview, flush=True)
            raise SystemExit(interviewed)  # No provisioning or hardware acceptance on stale build.
        if profile.get('require_pm'):
            import uuid
            if profile['postflash_role'] == 'Router':
                post = work / ('postflash_' + uuid.uuid4().hex + '.json')
                post_code = subprocess.call(postflash_cmd(profile, post))
                audit = work / ('pm_role_audit_' + uuid.uuid4().hex + '.json')
                audit_code = subprocess.call(role_audit_cmd(profile, audit))
                raise SystemExit(0 if post_code == 0 and audit_code == 0 else 2)
            pm_evidence = work / ('pm_provision_' + uuid.uuid4().hex + '.json')
            provisioned = subprocess.call(provision_cmd(profile, args.confirm_ieee, pm_evidence))
            if provisioned: raise SystemExit(provisioned)
            post_evidence = work / ('postflash_' + uuid.uuid4().hex + '.json')
            raise SystemExit(subprocess.call(postflash_cmd(profile, post_evidence)))
        post = work / ('postflash_' + uuid.uuid4().hex + '.json')
        raise SystemExit(subprocess.call(postflash_cmd(profile, post)))
    elif args.confirm_ieee:
        raise SystemExit('--confirm-ieee may only be supplied for flash, transition, rejoin, metadata, reinterview or provision-pm')
    raise SystemExit(subprocess.call(runner_args(profile, args.mode)))


if __name__ == '__main__':
    main()
