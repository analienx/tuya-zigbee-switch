#!/usr/bin/env python3
"""Deploy the CI-built Zigbee2MQTT converter to the live add-on, verified and reversible.

Dry run by default; --apply performs it. Refuses while any BSEED OTA lease is
held (a restart kills transfers), when the artifact differs from its CI
SHA256SUMS or from the converter committed on this checkout, or when the
running zigbee-herdsman(-converters) versions differ from the CI pins in
tests/zhc_runtime/package.json (the versions the converter tests ran on).
Backs up the deployed file, restarts once, checks the start log and restores
the backup (and restarts again) if the converter did not load.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONVERTER = 'switch_custom.js'
PINNED = ('zigbee-herdsman', 'zigbee-herdsman-converters')


class Refused(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def artifact_sha(artifact_dir, repo_converter):
    """The artifact converter, its CI SHA256SUMS entry and the committed file must agree."""
    artifact_dir = Path(artifact_dir)
    data = (artifact_dir / CONVERTER).read_bytes()
    sums = {}
    for line in (artifact_dir / 'SHA256SUMS').read_text(encoding='utf8').splitlines():
        digest, _, name = line.strip().partition('  ')
        if digest and name:
            sums[Path(name).name] = digest
    if sums.get(CONVERTER) != sha256(data):
        raise Refused('Artifact converter differs from its CI SHA256SUMS')
    if sha256(Path(repo_converter).read_bytes()) != sha256(data):
        raise Refused('Artifact converter differs from the committed ' + str(repo_converter))
    return sha256(data)


def pinned_versions(repo=ROOT):
    deps = json.loads((Path(repo) / 'tests/zhc_runtime/package.json').read_text(encoding='utf8'))['dependencies']
    return {name: deps[name] for name in PINNED}


def version_drift(pinned, running):
    return {name: {'ci': pinned[name], 'running': running.get(name)}
            for name in PINNED if running.get(name) != pinned[name]}


def active_leases(lock_dir):
    return sorted(p.name for p in Path(lock_dir).glob('bseed-zigbee-*.json'))


def start_log_verdict(log_text):
    """Judge only the latest start: the converter loaded and Zigbee2MQTT started."""
    lines = log_text.splitlines()
    starts = [i for i, line in enumerate(lines) if 'Starting Zigbee2MQTT' in line]
    if not starts:
        return 'no_start'
    tail = lines[starts[-1]:]
    if any('Failed to load external converter' in line and CONVERTER in line for line in tail):
        return 'converter_failed'
    if not any("Loaded external converter '%s'" % CONVERTER in line for line in tail):
        return 'pending' if not any('Zigbee2MQTT started' in line for line in tail) else 'converter_missing'
    return 'ok' if any('Zigbee2MQTT started' in line for line in tail) else 'pending'


class Host:
    def __init__(self, alias, slug, remote_dir):
        self.alias, self.slug, self.remote_dir = alias, slug, remote_dir.rstrip('/')

    def run(self, command, check=True):
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', self.alias, command],
                                capture_output=True, text=True, timeout=300)
        if check and result.returncode:
            raise RuntimeError('remote command failed: %s: %s' % (command.split()[0], result.stderr[-300:]))
        return result.stdout

    def running_versions(self):
        container = "$(docker ps --format '{{.Names}}' | grep -i zigbee2mqtt | head -1)"
        out = {}
        for name in PINNED:
            text = self.run('docker exec %s cat /app/node_modules/%s/package.json' % (container, name))
            out[name] = json.loads(text)['version']
        return out

    def remote_sha(self, path):
        return self.run('sha256sum %s' % shlex.quote(path)).split()[0]

    def upload(self, local, remote):
        if subprocess.call(['scp', '-q', '-o', 'BatchMode=yes', str(local), self.alias + ':' + remote]):
            raise RuntimeError('upload failed')

    def restart_and_verify(self, wait_seconds):
        self.run('ha apps restart %s' % shlex.quote(self.slug))
        deadline = time.monotonic() + wait_seconds
        verdict = 'pending'
        while time.monotonic() < deadline:
            time.sleep(10)
            verdict = start_log_verdict(self.run('ha apps logs %s' % shlex.quote(self.slug), check=False))
            if verdict not in ('pending', 'no_start'):
                break
        return verdict


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact-dir', required=True, help='Downloaded z2m-converter-canary-<sha> artifact')
    p.add_argument('--site', required=True, help='Private site.json (ha_ssh_alias, network_lock_dir)')
    p.add_argument('--addon', default='45df7312_zigbee2mqtt')
    p.add_argument('--remote-dir', default='/config/zigbee2mqtt/external_converters')
    p.add_argument('--wait-seconds', type=int, default=240)
    p.add_argument('--apply', action='store_true', help='Deploy and restart (default: dry run)')
    a = p.parse_args(argv)
    site = json.loads(Path(a.site).read_text(encoding='utf8'))
    try:
        digest = artifact_sha(a.artifact_dir, ROOT / 'zigbee2mqtt/converters' / CONVERTER)
        leases = active_leases(site['network_lock_dir'])
        if leases:
            raise Refused('OTA lease held, a restart would kill the transfer: ' + ', '.join(leases))
        host = Host(site['ha_ssh_alias'], a.addon, a.remote_dir)
        drift = version_drift(pinned_versions(), host.running_versions())
        if drift:
            raise Refused('Running converter libraries differ from the CI pins: %s' % json.dumps(drift))
        live = a.remote_dir + '/' + CONVERTER
        current = host.remote_sha(live)
        print('ARTIFACT', digest, 'DEPLOYED', current)
        if current == digest:
            print('UP_TO_DATE')
            return 0
        # Re-check right before acting: a campaign may have started meanwhile.
        if active_leases(site['network_lock_dir']):
            raise Refused('OTA lease appeared; not restarting')
        if not a.apply:
            print('DRY_RUN would back up, install and restart', a.addon)
            return 0
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = '%s.pre-deploy-%s' % (live, stamp)
        host.upload(Path(a.artifact_dir) / CONVERTER, live + '.new')
        if host.remote_sha(live + '.new') != digest:
            raise RuntimeError('uploaded converter hash differs')
        host.run('cp -p %s %s && mv %s %s' % (shlex.quote(live), shlex.quote(backup),
                                              shlex.quote(live + '.new'), shlex.quote(live)))
        verdict = host.restart_and_verify(a.wait_seconds)
        if verdict != 'ok':
            host.run('cp -p %s %s' % (shlex.quote(backup), shlex.quote(live)))
            rollback = host.restart_and_verify(a.wait_seconds)
            print('ROLLED_BACK', verdict, 'restored', backup, 'restart', rollback)
            return 3
        print('DEPLOYED', digest, 'backup', backup)
        return 0
    except Refused as error:
        print('REFUSED', error)
        return 2


if __name__ == '__main__':
    sys.exit(main())
