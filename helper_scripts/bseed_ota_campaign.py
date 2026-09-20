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
                file_version=int(str(profile['file_version']), 0))
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


def runner_args(profile, mode):
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
    return [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_z2m_postflash_verify.py'),
            '--device', profile['device'], '--ieee', profile['ieee'],
            '--expect-role', profile['postflash_role'], '--expect-build', profile['postflash_build'],
            '--mqtt-config', profile['mqtt_config'], '--broker', profile['broker'],
            '--output', str(evidence), '--observe-seconds', str(profile.get('observe_seconds', 20))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='Private JSON profile outside git')
    parser.add_argument('--mode', choices=['prepare', 'preflight', 'check', 'flash', 'transition', 'rejoin', 'metadata', 'postflash', 'status'], required=True)
    parser.add_argument('--confirm-ieee', help='Required for flash, transition and rejoin; must match profile IEEE exactly')
    args = parser.parse_args()
    profile = load_profile(args.profile)
    work = Path(profile['workdir'])
    if args.mode == 'prepare':
        print(json.dumps(make_index(profile), indent=2))
        return
    if args.mode == 'status':
        for filename in ('ACTIVE_LOCK.json', 'LAST_CHECK.json'):
            f = work / filename
            print(filename, f.read_text(encoding='utf8') if f.exists() else 'not present')
        return
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
        if args.mode == 'transition':
            post_evidence = work / ('postflash_' + uuid.uuid4().hex + '.json')
            raise SystemExit(subprocess.call(postflash_cmd(profile, post_evidence)))
        return
    if args.mode == 'flash':
        if args.confirm_ieee != profile['ieee']:
            raise SystemExit('Flash refused: supply --confirm-ieee with the exact target IEEE')
        if profile['preflash_role'] != profile['postflash_role']:
            raise SystemExit('Cross-role flash refused: use --mode transition for scoped rejoin')
        print('EXPLICIT_FLASH_TARGET', profile['ieee'], profile['device'], flush=True)
    elif args.confirm_ieee:
        raise SystemExit('--confirm-ieee may only be supplied for flash, transition, rejoin or metadata')
    raise SystemExit(subprocess.call(runner_args(profile, args.mode)))


if __name__ == '__main__':
    main()
