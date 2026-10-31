"""One-command BSEED same-role hardware canary.

Derives the private campaign profile from a private site file and the sealed
candidate, serves the image and single-target index on the LAN, then drives the
existing gated campaign (bseed_ota_campaign.py) in the right order for the
board. It adds no new device action and never retries.

    plan  write the private profile and served files; no network contact
    run   plan, serve, prepare -> preflight -> check (PM) or qualify (non-PM);
          stops there unless --flash with the exact --confirm-ieee is given

Cross-role conversions keep using bseed_ota_campaign.py --mode transition.
"""
import argparse
import datetime as dt
import functools
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

from bseed_ota_identity import DEFAULT_REGISTRY, load_registry, parse_ota_header  # noqa: E402

BOARDS = {
    'pm': {'manufacturer': 'b28wrpvx', 'model': 'TS011F-BS-PM', 'release': 'bseed_pm_release'},
    'nonpm': {'manufacturer': 'o1jzcxou', 'model': 'TS011F-BS', 'release': 'bseed_nonpm_release'},
}
ROLE_KEYS = {'Router': 'router', 'EndDevice': 'client'}
SITE_REQUIRED = ('mqtt_config', 'broker', 'serve_root', 'serve_host', 'serve_port',
                 'campaign_root', 'network_lock_dir', 'network_id', 'devices')
DEVICE_REQUIRED = ('ieee', 'board', 'role', 'preflash_build', 'expect_relay')
# Conservative transfer pacing evidenced by the 2026-09-24 sleepy-client
# recovery; a device entry may override these, never the identity fields.
PACING = {'block_bytes': 32, 'response_delay_ms': 1200,
          'request_timeout_ms': 1800000, 'monitor_seconds': 14400,
          'check_timeout_seconds': 90}
IDENTITY = {'device', 'ieee', 'manufacturer', 'model', 'preflash_role', 'postflash_role',
            'image', 'sha256', 'url', 'manufacturer_code', 'image_type', 'file_version',
            'postflash_build', 'require_pm', 'non_pm', 'force_test_transition',
            'index_url', 'index_output', 'template_index', 'workdir'}
CHECKLIST = {
    'pm': ['Loaded-to-zero metering test (docs/bseed_pm_e2e_acceptance.md)',
           'Relay ON/OFF with a known safe load; physical state matches Zigbee2MQTT',
           'Cumulative energy did not regress across the OTA reboot'],
    'nonpm': ['Relay ON/OFF with a known safe load; physical state matches Zigbee2MQTT',
              'No PM reporting is exposed on the non-PM board'],
    'Router': ['A sleepy child (e.g. IKEA RODRET) still joins and routes via this socket'],
    'EndDevice': ['Parent stays stable; repeated relay reads answer within normal latency'],
}


class CanaryError(Exception):
    pass


def outside_repo(path, what):
    resolved = Path(path).expanduser().resolve()
    if resolved == ROOT or resolved.is_relative_to(ROOT):
        raise CanaryError('%s must be private, outside the repository: %s' % (what, resolved))
    return resolved


def load_site(path):
    site_path = outside_repo(path, 'Site file')
    site = json.loads(site_path.read_text(encoding='utf8'))
    missing = [key for key in SITE_REQUIRED if site.get(key) in (None, '')]
    if missing:
        raise CanaryError('Site file missing: ' + ', '.join(missing))
    for key in ('serve_root', 'campaign_root', 'network_lock_dir', 'mqtt_config'):
        site[key] = str(outside_repo(site[key], key))
    return site


def device_entry(site, name):
    device = site['devices'].get(name)
    if not isinstance(device, dict):
        raise CanaryError('Device not in site roster: ' + name)
    missing = [key for key in DEVICE_REQUIRED if device.get(key) in (None, '')]
    if missing:
        raise CanaryError('Device %s missing: %s' % (name, ', '.join(missing)))
    if device['board'] not in BOARDS:
        raise CanaryError('Device board must be pm or nonpm')
    if device['role'] not in ROLE_KEYS:
        raise CanaryError('Device role must be Router or EndDevice')
    ieee = str(device['ieee'])
    if not ieee.startswith('0x') or len(ieee) != 18:
        raise CanaryError('Device IEEE must be the full 0x-prefixed address')
    overrides = device.get('overrides', {})
    blocked = sorted(IDENTITY.intersection(overrides))
    if blocked:
        raise CanaryError('Overrides may not change identity fields: ' + ', '.join(blocked))
    return device


def candidate(board, role):
    module = __import__(BOARDS[board]['release'])
    return module.CANDIDATES[ROLE_KEYS[role]]


def sealed_artifact(artifacts, board, role, cand, registry):
    """Return the exact sealed bytes, refusing anything the registry does not seal."""
    package_path = Path(artifacts) / 'CANDIDATE_PACKAGE.json'
    if not package_path.is_file():
        raise CanaryError('Artifacts directory lacks CANDIDATE_PACKAGE.json')
    variant = '%s-%s' % (board, ROLE_KEYS[role])
    entries = [e for e in json.loads(package_path.read_text(encoding='utf8')).get('entries', [])
               if e.get('variant') == variant]
    if len(entries) != 1:
        raise CanaryError('Candidate package has no unique %s entry' % variant)
    entry = entries[0]
    if (entry.get('build'), entry.get('imageType'), entry.get('fileVersion')) != (
            cand['build'], cand['type'], cand['version']):
        raise CanaryError('Package %s is not the current release candidate %s' % (
            variant, cand['build']))
    data = (Path(artifacts) / variant / 'firmware.ota').read_bytes()
    sha512 = hashlib.sha512(data).hexdigest()
    sealed = [v for v in registry.get(cand['type'], {}).get('versions', [])
              if v.get('file_version') == cand['version'] and v.get('version_str') == cand['build']]
    if len(sealed) != 1 or not sealed[0].get('sha512'):
        raise CanaryError('Candidate %s is not sealed in the identity registry' % cand['build'])
    if sha512 != sealed[0]['sha512'] or sha512 != entry.get('otaSha512'):
        raise CanaryError('Artifact bytes differ from the sealed registry identity')
    header = parse_ota_header(data)
    if (header['manufacturer_code'], header['image_type'], header['file_version']) != (
            4417, cand['type'], cand['version']):
        raise CanaryError('OTA header does not match the sealed identity')
    return data, entry


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise CanaryError('Refusing to overwrite different private file: %s' % path)
    path.write_bytes(data)


# Phases that do not block a new campaign for the same socket. Anything else
# (update_error, ota_transfer_ok_postflash_unverified, update_timeout...) is an
# unresolved campaign that a new per-build workdir must not silently bypass.
CLEAN_LOCK_PHASES = ('postflash_candidate',)


def prior_campaign_locks(site, name):
    """Refuse while any earlier campaign for this socket is unresolved.

    Each build gets its own workdir, so the new campaign cannot see an earlier
    device lock by itself. Scan the roster's prior workdirs and the canary root.
    """
    device = device_entry(site, name)
    roots = [Path(site['campaign_root']) / name]
    roots += [outside_repo(p, 'prior_workdirs') for p in device.get('prior_workdirs', [])]
    for root in roots:
        if not root.exists():
            continue
        for lock in sorted(root.rglob('ACTIVE_LOCK.json')):
            phase = json.loads(lock.read_text(encoding='utf8')).get('phase')
            if phase not in CLEAN_LOCK_PHASES:
                raise CanaryError('Unresolved campaign lock for %s at %s (phase %s); '
                                  'reconcile or abandon it deliberately first'
                                  % (name, lock, phase))


def build_profile(site, name, artifacts, registry=None):
    registry = load_registry(DEFAULT_REGISTRY) if registry is None else registry
    device = device_entry(site, name)
    prior_campaign_locks(site, name)
    board, role = device['board'], device['role']
    cand = candidate(board, role)
    data, entry = sealed_artifact(artifacts, board, role, cand, registry)
    spec = BOARDS[board]
    base = 'http://%s:%d' % (site['serve_host'], int(site['serve_port']))
    image_name = '%s-%s.ota' % (name, cand['build'])
    index_name = 'index-%s-%s.json' % (name, cand['build'])
    serve_root = Path(site['serve_root'])
    workdir = Path(site['campaign_root']) / name / cand['build']
    image = serve_root / image_name
    write_once(image, data)
    template = [{
        'fileName': image_name, 'fileVersion': cand['version'], 'fileSize': len(data),
        'url': base + '/' + image_name, 'imageType': cand['type'], 'manufacturerCode': 4417,
        'sha512': hashlib.sha512(data).hexdigest(), 'manufacturerName': [spec['manufacturer']],
        'otaHeaderString': data[20:52].decode('latin1')}]
    template_path = workdir / 'template_index.json'
    write_once(template_path, (json.dumps(template, indent=2) + '\n').encode('utf8'))
    profile = dict(PACING)
    profile.update({
        'device': name, 'ieee': device['ieee'],
        'manufacturer': spec['manufacturer'], 'model': spec['model'],
        'preflash_role': role, 'postflash_role': role,
        'preflash_build': device['preflash_build'], 'postflash_build': cand['build'],
        'image': str(image), 'sha256': hashlib.sha256(data).hexdigest(),
        'url': base + '/' + image_name, 'index_url': base + '/' + index_name,
        'index_output': str(serve_root / index_name), 'template_index': str(template_path),
        'mqtt_config': site['mqtt_config'], 'broker': site['broker'], 'workdir': str(workdir),
        'network_lock_dir': site['network_lock_dir'], 'network_id': site['network_id'],
        'network_lock_shared': True, 'manufacturer_code': 4417,
        'image_type': cand['type'], 'file_version': '0x%08x' % cand['version'],
        'expect_relay': device['expect_relay'], 'force_test_transition': False,
        'require_pm': board == 'pm', 'non_pm': board == 'nonpm',
    })
    if board == 'pm':
        for key in ('pm_ssh_host', 'pm_ssh_key'):
            if not site.get(key):
                raise CanaryError('PM canary requires site ' + key)
            profile[key] = site[key]
        if role == 'Router':
            profile['relay_get_key'] = 'state_relay'
            if not device.get('pm_settings_baseline'):
                raise CanaryError('PM Router canary requires a pm_settings_baseline capture')
            profile['pm_settings_baseline'] = str(outside_repo(
                device['pm_settings_baseline'], 'pm_settings_baseline'))
            matrix = {'hardwareAcceptance': False, 'compiledBothRoles': True,
                      'sourceCommit': entry.get('sourceCommit'),
                      'artifacts': [{'role': r, 'build': candidate('pm', r)['build'],
                                     'imageType': candidate('pm', r)['type'],
                                     'sha256': sealed_artifact(artifacts, 'pm', r,
                                                               candidate('pm', r),
                                                               registry)[1]['otaSha256']}
                                    for r in ('Router', 'EndDevice')]}
            matrix_path = workdir / 'pm_router_matrix.json'
            write_once(matrix_path, (json.dumps(matrix, indent=2) + '\n').encode('utf8'))
            profile['pm_router_matrix_evidence'] = str(matrix_path)
    else:
        if not device.get('relay_physical_mode'):
            raise CanaryError('Non-PM canary requires the device relay_physical_mode')
        profile['preflash_relay_physical_mode'] = device['relay_physical_mode']
        if device.get('recovery_evidence'):
            profile['recovery_evidence'] = str(outside_repo(
                device['recovery_evidence'], 'recovery_evidence'))
    profile.update(device.get('overrides', {}))
    profile_path = workdir / 'profile.json'
    write_once(profile_path, (json.dumps(profile, indent=2, sort_keys=True) + '\n').encode('utf8'))
    return profile_path, profile


class LanServer:
    """Serve only the private serve_root for the duration of one canary."""

    def __init__(self, root, bind, port):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer((bind, int(port)), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def stages(profile, flash, confirm_ieee, confirm_unloaded):
    sequence = [('prepare', []), ('preflight', [])]
    sequence.append(('qualify', []) if profile['non_pm'] else ('check', []))
    if flash:
        extra = ['--confirm-ieee', confirm_ieee]
        if confirm_unloaded:
            extra.append('--confirm-load-unplugged')
        sequence += [('flash', extra), ('status', [])]
    return sequence


def require_flash_confirmation(profile, confirm_ieee, confirm_unloaded):
    if confirm_ieee != profile['ieee']:
        raise CanaryError('Flash refused: --confirm-ieee must equal %s exactly' % profile['ieee'])
    physical = profile['non_pm'] or profile.get('pm_preflash_load_proof') == 'physically_unloaded'
    if physical and not confirm_unloaded:
        raise CanaryError('Flash refused: this board needs a fresh --confirm-load-unplugged')
    if not physical and confirm_unloaded:
        raise CanaryError('--confirm-load-unplugged applies only to physical-proof boards')


def run(site, profile_path, profile, *, flash=False, confirm_ieee=None,
        confirm_unloaded=False, call=subprocess.call):
    if flash:
        require_flash_confirmation(profile, confirm_ieee, confirm_unloaded)
    elif confirm_ieee or confirm_unloaded:
        raise CanaryError('Confirmations are only accepted together with --flash')
    campaign = [sys.executable, '-u', str(ROOT / 'helper_scripts/bseed_ota_campaign.py'),
                '--profile', str(profile_path)]
    with LanServer(site['serve_root'], site.get('serve_bind', '0.0.0.0'), site['serve_port']):
        for mode, extra in stages(profile, flash, confirm_ieee, confirm_unloaded):
            print('CANARY_STAGE', mode, profile['device'], flush=True)
            status = call(campaign + ['--mode', mode] + extra)
            if status:
                print('CANARY_STOPPED', mode, 'exit', status, 'no retry', flush=True)
                return status
    if not flash:
        print('CANARY_QUALIFIED_NO_FLASH', profile['device'], profile['postflash_build'], flush=True)
        return 0
    print('CANARY_SOFTWARE_GATES_PASSED', profile['device'], profile['postflash_build'],
          dt.datetime.now(dt.timezone.utc).isoformat(), flush=True)
    print('Hardware acceptance still requires, on this socket:')
    for item in CHECKLIST['pm' if profile['require_pm'] else 'nonpm'] + CHECKLIST[profile['postflash_role']]:
        print('  - ' + item)
    print('  - 24 h soak before advancing to another socket')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run'])
    parser.add_argument('--site', required=True, help='Private site file outside git')
    parser.add_argument('--device', required=True, help='Device name from the site roster')
    parser.add_argument('--artifacts', required=True,
                        help='Downloaded bseed-flash-candidates-<sha> directory')
    parser.add_argument('--flash', action='store_true', help='Submit the OTA after all read-only gates')
    parser.add_argument('--confirm-ieee', help='Exact target IEEE, required with --flash')
    parser.add_argument('--confirm-load-unplugged', action='store_true',
                        help='Non-PM: operator just verified no appliance is attached')
    args = parser.parse_args(argv)
    try:
        site = load_site(args.site)
        profile_path, profile = build_profile(site, args.device, args.artifacts)
        print('CANARY_PROFILE', profile_path, flush=True)
        if args.command == 'plan':
            if args.flash or args.confirm_ieee or args.confirm_load_unplugged:
                raise CanaryError('plan never flashes; use run --flash')
            for mode, _ in stages(profile, True, '<ieee>', profile['non_pm']):
                print('  would run', mode)
            return 0
        return run(site, profile_path, profile, flash=args.flash, confirm_ieee=args.confirm_ieee,
                   confirm_unloaded=args.confirm_load_unplugged)
    except (CanaryError, OSError, ValueError) as error:
        print('CANARY_REFUSED', error, file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
