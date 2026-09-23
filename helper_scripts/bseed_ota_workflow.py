"""BSEED OTA operator v1.0.0: inspect, serve, qualify, then separately authorize flash.

Reuses the canonical campaign/transport/recovery gates; never retries an OTA.
Private profile and all evidence remain outside the repository. This is not a
fleet updater or a generic updater for unrelated Zigbee firmware.
"""
import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, unquote
import urllib.request

from bseed_ota_campaign import load_profile, make_index

VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {'ota_running', 'update_pending', 'update_timeout_or_unconfirmed',
          'ota_transfer_ok_postflash_unverified', 'update_ok'}
CAMPAIGN = ROOT / 'helper_scripts' / 'bseed_ota_campaign.py'


def image_contract(profile):
    image = Path(profile['image']).resolve()
    index = Path(profile['index_output']).resolve()
    if image.parent != index.parent or image == index:
        raise ValueError('Image and private one-entry index must share an isolated server directory')
    for field, target in (('url', image), ('index_url', index)):
        parsed = urlsplit(profile[field])
        if (parsed.scheme != 'http' or not parsed.hostname or not parsed.port
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or unquote(parsed.path) != '/' + target.name):
            raise ValueError('Private HTTP URL must identify its exact image or index filename: ' + field)
    image_url, index_url = urlsplit(profile['url']), urlsplit(profile['index_url'])
    if (image_url.hostname, image_url.port) != (index_url.hostname, index_url.port):
        raise ValueError('Image and index URLs must use the same private HTTP endpoint')
    data = image.read_bytes()
    if hashlib.sha256(data).hexdigest() != profile['sha256']:
        raise ValueError('Pinned firmware checksum differs from local image')
    return image, index, data


def check_served(profile):
    _, index, data = image_contract(profile)
    with urllib.request.urlopen(profile['url'], timeout=12) as response:
        served = response.read(len(data) + 1)
    if served != data:
        raise ValueError('HTTP-served firmware does not match the pinned local image')
    with urllib.request.urlopen(profile['index_url'], timeout=12) as response:
        served_index = response.read(131073)
    if len(served_index) > 131072 or served_index != index.read_bytes():
        raise ValueError('HTTP-served index does not match the private index')
    entries = json.loads(served_index)
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError('OTA index must contain exactly one firmware entry')
    if (entries[0].get('url') != profile['url'] or
            entries[0].get('sha512') != hashlib.sha512(data).hexdigest()):
        raise ValueError('OTA index URL or SHA-512 differs from pinned firmware')
    return True


def inspect_locks(profile, campaign_root, *, acknowledge_failures=False):
    """Historical failures require operator acknowledgment; active jobs always block.

    This scans only the supplied private campaign root. It cannot prove that
    unrelated OTA clients outside that root are idle; live preflight is separate.
    """
    root = Path(campaign_root).resolve()
    work = Path(profile['workdir']).resolve()
    if not work.is_relative_to(root) or root.is_relative_to(ROOT):
        raise ValueError('Workdir must be inside an explicit private campaign root')
    prior = []
    for path in sorted(root.rglob('ACTIVE_LOCK.json')):
        try:
            record = json.loads(path.read_text(encoding='utf8'))
        except (OSError, ValueError) as exc:
            raise ValueError('Unparseable OTA campaign lock: ' + str(path)) from exc
        phase = record.get('phase')
        if path.parent.resolve() == work and phase not in (None, 'postflash_accepted'):
            raise ValueError('This campaign already has a lock: ' + str(phase))
        if phase in ACTIVE:
            raise ValueError('Unresolved OTA campaign requires independent review: ' + str(path))
        if record.get('ieee', '').lower() == profile['ieee'].lower() and phase == 'update_error':
            prior.append(str(path))
    if prior and not acknowledge_failures:
        raise ValueError('Previous failed OTA on exact IEEE; review logs and pass --ack-prior-failures')
    return prior


def campaign(profile_path, mode, *, confirm_ieee=None, unloaded=False, accept_risk=False):
    command = [sys.executable, '-u', str(CAMPAIGN), '--profile', str(profile_path), '--mode', mode]
    if mode == 'flash':
        command += ['--confirm-ieee', confirm_ieee]
        if unloaded: command.append('--confirm-load-unplugged')
        if accept_risk: command.append('--accept-nonrecoverable-ota-risk')
    subprocess.run(command, check=True)


def ready(profile_path, profile, root, acknowledge):
    inspect_locks(profile, root, acknowledge_failures=acknowledge)
    image_contract(profile)
    make_index(profile)  # Reuses native OTA/header/version/hash validation.
    check_served(profile)
    campaign(profile_path, 'preflight')
    campaign(profile_path, 'qualify' if profile.get('non_pm') is True else 'check')
    # A separate transfer is never inferred from a completed qualification.
    check_served(profile)
    print('QUALIFIED_NO_FLASH', profile['device'], profile['ieee'], flush=True)


def serve(profile, bind):
    image, index, _ = image_contract(profile)
    address = urlsplit(profile['url'])
    if bind != address.hostname:
        raise ValueError('Serve only on the exact private URL hostname (no wildcard bind)')
    allowed = {'/' + image.name, '/' + index.name}
    class ScopedHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(image.parent), **kwargs)
        def do_GET(self):
            if unquote(urlsplit(self.path).path) not in allowed:
                self.send_error(404)
                return
            super().do_GET()
        def do_HEAD(self):
            if unquote(urlsplit(self.path).path) not in allowed:
                self.send_error(404)
                return
            super().do_HEAD()
    with ThreadingHTTPServer((bind, address.port), ScopedHandler) as server:
        print('PRIVATE_OTA_SERVER_READY', bind, address.port, flush=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='bseed-ota-workflow ' + VERSION)
    parser.add_argument('--profile', required=True, help='Private campaign JSON outside git')
    parser.add_argument('--campaign-root', required=True, help='Private root containing ALL campaign locks for this coordinator')
    parser.add_argument('--mode', required=True, choices=('audit', 'serve', 'qualify', 'flash'))
    parser.add_argument('--bind', help='For serve: exact hostname/IP from private image URL')
    parser.add_argument('--ack-prior-failures', action='store_true', help='Operator reviewed prior failed OTA logs and live device')
    parser.add_argument('--confirm-ieee', help='For flash only: exact target IEEE')
    parser.add_argument('--confirm-load-unplugged', action='store_true', help='For non-PM flash only: operator JUST checked physical socket')
    parser.add_argument('--accept-nonrecoverable-ota-risk', action='store_true', help='For non-PM flash only: explicitly accept possible permanent loss')
    args = parser.parse_args()
    profile_path = Path(args.profile).expanduser().resolve()
    profile = load_profile(profile_path)
    if args.mode != 'flash' and (args.confirm_ieee or args.confirm_load_unplugged or args.accept_nonrecoverable_ota_risk):
        parser.error('Physical/risk/IEEE flash flags are valid only for flash mode')
    if args.mode == 'serve':
        if args.ack_prior_failures: parser.error('Do not acknowledge old campaigns through the server command')
        inspect_locks(profile, args.campaign_root, acknowledge_failures=True)
        serve(profile, args.bind)
        return
    historical = inspect_locks(profile, args.campaign_root, acknowledge_failures=args.ack_prior_failures)
    print('OTA_WORKFLOW', VERSION, 'device', profile['device'], 'prior_failed', len(historical), flush=True)
    if args.mode == 'audit':
        image_contract(profile)
        print('AUDIT_NO_FLASH', profile['device'], flush=True)
        return
    if args.mode == 'flash':
        if args.confirm_ieee != profile['ieee']:
            parser.error('An exact --confirm-ieee is mandatory for a flash')
        if profile.get('non_pm') is True and not (args.confirm_load_unplugged and args.accept_nonrecoverable_ota_risk):
            parser.error('Non-PM flash needs freshly checked physical unloading and explicit nonrecoverable-risk acceptance')
    ready(profile_path, profile, args.campaign_root, args.ack_prior_failures)
    if args.mode == 'flash':
        # The canonical runner independently verifies evidence freshness, exact
        # IEEE, risk/physical gates and update state at the time of submission.
        campaign(profile_path, 'flash', confirm_ieee=args.confirm_ieee,
                 unloaded=args.confirm_load_unplugged, accept_risk=args.accept_nonrecoverable_ota_risk)


if __name__ == '__main__':
    main()
