"""Explicit, audited close of a COMPLETED Zigbee OTA transfer (not hardware acceptance).

This is a private evidence contract, not an OTA sender or a policy override.
The independent quiet-window observer and operator approval are REQUIRED inputs.
Do not run against a live network until that observer, approval and execution
route are separately reviewed. No default or TTL-driven release exists.

A pending transaction blocks acquisition; after a process crash it requires
explicit recovery rather than quietly becoming a free network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from bseed_network_campaign_lock import read_lock
from bseed_network_mutex import network_mutex, pending_path

COMPLETED = frozenset(('ota_transfer_ok_postflash_unverified', 'postflash_candidate'))
ACTION = 'retire-completed-transfer-with-unaccepted-hardware'
QUIET_SOURCE = 'independent-zigbee2mqtt-readonly-inspection-v1'
UTC = dt.timezone.utc


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing offset-aware observation timestamp')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone-aware observation timestamps are mandatory')
    return result.astimezone(UTC)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    return digest_bytes(Path(path).read_bytes())


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf8')


def load_private(path, repo_root):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.is_relative_to(Path(repo_root).resolve()):
        raise ValueError('Require an existing PRIVATE evidence file outside repository')
    return path, json.loads(path.read_text(encoding='utf8'))


def require_identity(network, work, profile):
    if not isinstance(network, dict) or network.get('schema') != 1:
        raise ValueError('Exact shared authority record required')
    fields = (('network_id', profile['network_id']),
              ('device', profile['device']), ('ieee', profile['ieee']),
              ('image_sha256', profile['sha256']))
    for key, value in fields:
        if network.get(key) != value:
            raise ValueError('Network identity mismatch: ' + key)
    if network.get('phase') not in COMPLETED:
        raise ValueError('Only completed-but-unaccepted transfers can terminalize')
    for key, value in (('device', profile['device']), ('ieee', profile['ieee']),
                       ('sha256', profile['sha256']), ('token', network.get('token'))):
        if work.get(key) != value:
            raise ValueError('Campaign work-lock mismatch: ' + key)
    if work.get('phase') not in COMPLETED:
        raise ValueError('Work lock is not transfer-completed')
    response = work.get('response') or {}
    data = response.get('data') or {}
    if (response.get('status') != 'ok' or
            response.get('transaction') != network.get('token') or
            data.get('id') not in (profile['ieee'], profile['device']) or
            (data.get('to') or {}).get('software_build_id') != profile['postflash_build']):
        raise ValueError('Exact successful OTA transport response/build required')
    if digest_file(profile['image']) != profile['sha256']:
        raise ValueError('Candidate image bytes differ from sealed source')
    return True


def validate_evidence(profile, network, work, quiet, post, approval, *, now=None):
    """Validate inputs WITHOUT changing authority. Refuses missing evidence.

    Quiet proof must be supplied by an independently reviewed live observer.
    A handwritten JSON assertion is not by itself evidence that no OTA is active.
    """
    require_identity(network, work, profile)
    now = now or dt.datetime.now(UTC)
    if (approval.get('action'), approval.get('network_id'),
            approval.get('token'), approval.get('ieee'),
            approval.get('image_sha256')) != (
            ACTION, profile['network_id'], network['token'],
            profile['ieee'], profile['sha256']):
        raise ValueError('Explicit approval does not match exact transaction')
    if not isinstance(approval.get('operator'), str) or not approval['operator'].strip():
        raise ValueError('Named operator approval required')
    if not 0 <= (now - stamp(approval.get('approved_at'))).total_seconds() <= 3600:
        raise ValueError('Approval is stale or from the future')
    if (quiet.get('source'), quiet.get('network_id')) != (
            QUIET_SOURCE, profile['network_id']):
        raise ValueError('Independent network-wide Zigbee2MQTT evidence required')
    begin, end = stamp(quiet.get('window_started_at')), stamp(quiet.get('window_ended_at'))
    duration = (end - begin).total_seconds()
    if not 90 <= duration <= 900 or not 0 <= (now - end).total_seconds() <= 300:
        raise ValueError('Stale, future or insufficiently observed OTA quiet window')
    if begin < stamp(network.get('completed')):
        raise ValueError('Quiet-window observation must start after transfer completion')
    if (quiet.get('bridge_online') is not True or quiet.get('permit_join') is not False
            or quiet.get('complete_device_inventory') is not True
            or quiet.get('active_ota_scan_complete') is not True
            or quiet.get('active_ota_transfers') != []
            or type(quiet.get('inventory_count')) is not int
            or quiet['inventory_count'] < 1):
        raise ValueError('Bridge, permit-join, full inventory or active-OTA check failed')
    target = quiet.get('target') or {}
    if (target.get('ieee'), target.get('name'), target.get('build'),
            target.get('role'), target.get('interview_completed')) != (
            profile['ieee'], profile['device'], profile['postflash_build'],
            profile['postflash_role'], True):
        raise ValueError('Fresh installed target identity/build/role not proven')
    if post.get('target_ieee') != profile['ieee'] or post.get('target_name') != profile['device']:
        raise ValueError('Postflash evidence does not match original target')
    if (post.get('expected_build'), post.get('expected_role')) != (
            profile['postflash_build'], profile['postflash_role']):
        raise ValueError('Postflash expected identity mismatch')
    inv = post.get('inventory') or {}
    if (inv.get('ieee_address'), inv.get('software_build_id'), inv.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('Postflash installed identity not proven')
    if (post.get('result') not in ('unconfirmed', 'postflash_candidate') or
            post.get('fresh_mqtt_state_observed') is not True):
        raise ValueError('Preserved unaccepted postflash evidence required')
    if post.get('errors'):
        raise ValueError('Postflash communication errors require separate investigation')
    if profile.get('require_pm') is True and not isinstance(
            (work.get('preflash_state') or {}).get('energy'), (int, float)):
        raise ValueError('Missing original PM energy baseline')
    return {
        'network_id': network['network_id'],
        'token': network['token'],
        'device': network['device'],
        'ieee': network['ieee'],
        'image_sha256': network['image_sha256'],
        'transport_phase': network['phase'],
        'transport_completed_at': network['completed'],
        'installed_build': profile['postflash_build'],
        'installed_role': profile['postflash_role'],
        'hardware_acceptance': 'unverified',
        'physical_acceptance': False,
        'network_ownership_state': 'terminalization_intent',
        'original_preflash_state': work.get('preflash_state'),
        'observed_postflash_state': post.get('state'),
        'postflash_findings': post.get('issues', []),
        'approval_operator': approval['operator'],
        'quiet_window_end': quiet['window_ended_at'],
    }


def _write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(json_bytes(value))
        f.flush()
        os.fsync(f.fileno())


def artifact_paths(authority, token):
    # Avoid exposing token in filenames. All paths are under private authority.
    authority = Path(authority)
    suffix = digest_bytes(token.encode('utf8'))[:24]
    prefix = authority.name + '.terminal-' + suffix
    return {
        'pending': pending_path(authority),
        'archive': authority.with_name(prefix + '.archived-lock.json'),
        'intent': authority.with_name(prefix + '.intent.json'),
        'committed': authority.with_name(prefix + '.committed.json'),
    }


def inspect(profile, network_path, quiet, post, approval, *, now=None):
    """Read-only proposal. No release, flashing or device commands."""
    network = read_lock(network_path)
    work_path = Path(profile['workdir']) / 'ACTIVE_LOCK.json'
    work = json.loads(work_path.read_text(encoding='utf8'))
    if (Path(profile['workdir']) / 'PM_TELEMETRY_GUARD.json').exists():
        raise ValueError('Active PM telemetry quarantine remains')
    return validate_evidence(profile, network, work, quiet, post, approval, now=now)


def commit(profile, network_path, quiet, post, approval, *,
           evidence_sha256, now=None):
    """Journal + immutable intent + archived authority + immutable commit receipt.

    Only for a separately authorized operation. Never triggers a subsequent OTA.
    An incomplete journal deliberately blocks new ownership on restart.
    """
    network_path = Path(network_path)
    with network_mutex(network_path):
        record = inspect(profile, network_path, quiet, post, approval, now=now)
        paths = artifact_paths(network_path, record['token'])
        if any(p.exists() for p in paths.values()):
            raise RuntimeError('Existing terminalization artifacts require audited recovery')
        original = network_path.read_bytes()
        original_sha = digest_bytes(original)
        record['original_lock_sha256'] = original_sha
        record['evidence_sha256'] = evidence_sha256
        record['recorded_at'] = (now or dt.datetime.now(UTC)).isoformat()
        # Persistent journal blocks new lock acquisition from this point.
        _write_once(paths['pending'], {
            'schema': 1, 'token': record['token'],
            'original_lock_sha256': original_sha,
            'intent': str(paths['intent']),
            'archive': str(paths['archive']),
            'committed': str(paths['committed']),
        })
        _write_once(paths['intent'], record)
        # All cooperating BSEED writers use the same mutex. A direct rename
        # preserves the original byte-for-byte network record for forensics.
        os.rename(network_path, paths['archive'])
        if digest_file(paths['archive']) != original_sha:
            raise RuntimeError('Archive integrity mismatch; leave pending blocker')
        done = dict(record, network_ownership_state='released_unaccepted')
        done['archived_network_lock_sha256'] = original_sha
        _write_once(paths['committed'], done)
        paths['pending'].unlink()
        return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profile', required=True)
    ap.add_argument('--quiet-evidence', required=True)
    ap.add_argument('--postflash-evidence', required=True)
    ap.add_argument('--operator-approval', required=True)
    ap.add_argument('--confirm-ieee', required=True)
    ap.add_argument('--mode', choices=('inspect', 'commit'), default='inspect')
    args = ap.parse_args(argv)
    from bseed_ota_campaign import ROOT, load_profile, network_lock_path
    profile = load_profile(args.profile)
    if args.confirm_ieee != profile['ieee']:
        raise ValueError('Exact original IEEE confirmation required')
    path = network_lock_path(profile, required=True)
    sources = [load_private(p, ROOT) for p in (
        args.quiet_evidence, args.postflash_evidence, args.operator_approval)]
    quiet, post, approval = [item[1] for item in sources]
    evidence_hashes = {
        item[0].name: digest_file(item[0]) for item in sources
    }
    if len(evidence_hashes) != 3:
        raise ValueError('Independent evidence must be in three distinct files')
    if args.mode == 'inspect':
        print(json.dumps(inspect(profile, path, quiet, post, approval), indent=2))
    else:
        raise SystemExit(
            'Live terminalization is not enabled by CLI until the independent '
            'Z2M quiet-window collector and platform execution route are reviewed. '
            'No lock released.')


if __name__ == '__main__':
    main()
