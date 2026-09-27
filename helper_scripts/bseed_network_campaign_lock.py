"""Network-scoped atomic ownership for BSEED OTA campaigns.

The lock directory is private operator state and must be the SAME shared,
atomic filesystem location for every runner host that can reach the Zigbee
network.  Work-directory locks remain evidence mirrors, not exclusion
authority.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile

SCHEMA = 1
ACCEPTED_PHASE = 'postflash_accepted'


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def authority_path(network_lock_dir, network_id):
    if not (isinstance(network_id, str) and network_id.strip()):
        raise ValueError('Stable Zigbee network_id is required')
    root = Path(network_lock_dir).expanduser().resolve()
    key = hashlib.sha256(network_id.strip().encode('utf8')).hexdigest()[:32]
    return root / ('bseed-zigbee-' + key + '.json')


def require_profile_authority(profile):
    if profile.get('network_lock_shared') is not True:
        raise ValueError(
            'network_lock_shared=true is required; all runner hosts must use '
            'the same atomic lock authority')
    directory = profile.get('network_lock_dir')
    network_id = profile.get('network_id')
    if not directory or not network_id:
        raise ValueError('network_lock_dir and network_id are required for OTA')
    work = Path(profile['workdir']).resolve()
    root = Path(directory).expanduser().resolve()
    if root == work or root.is_relative_to(work):
        raise ValueError('Network lock authority must be independent of workdir')
    return authority_path(root, network_id)


def read_lock(path):
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf8'))
    if not (isinstance(data, dict) and data.get('schema') == SCHEMA):
        raise ValueError('Invalid network lock record')
    return data


def acquire(path, *, network_id, token, device, ieee, image_sha256):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'schema': SCHEMA,
        'network_id': network_id,
        'token': token,
        'device': device,
        'ieee': ieee,
        'image_sha256': image_sha256,
        'phase': 'preflight_complete',
        'owner_host': socket.gethostname(),
        'acquired_at': now_iso(),
        'updated_at': now_iso(),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as error:
        existing = read_lock(path)
        raise RuntimeError(
            'Zigbee network already has campaign ownership: ' +
            json.dumps(existing, sort_keys=True)[:600]) from error
    with os.fdopen(fd, 'w', encoding='utf8') as handle:
        json.dump(record, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    return record


def update(path, token, phase, **extra):
    path = Path(path)
    current = read_lock(path)
    if current is None:
        raise RuntimeError('Network campaign lock disappeared')
    if current.get('token') != token:
        raise RuntimeError('Network campaign lock token mismatch')
    updated = dict(current)
    updated.update(extra)
    updated['phase'] = phase
    updated['updated_at'] = now_iso()
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump(updated, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return updated


def _release_phase(path, token, allowed_phase):
    current = read_lock(path)
    if current is None:
        raise RuntimeError('Network campaign lock disappeared before release')
    if current.get('token') != token:
        raise RuntimeError('Network campaign lock token mismatch')
    if current.get('phase') != allowed_phase:
        raise RuntimeError(
            'Network ownership may not release from phase ' +
            str(current.get('phase')))
    Path(path).unlink()
    return True


def release_preflight_abort(path, token):
    return _release_phase(path, token, 'preflight_abort')


def release_accepted(path, token):
    return _release_phase(path, token, ACCEPTED_PHASE)
