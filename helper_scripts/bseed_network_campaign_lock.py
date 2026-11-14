"""Network-scoped atomic ownership for BSEED OTA campaigns.

The lock directory is private operator state and must be the SAME shared,
atomic filesystem location for every runner host that can reach the Zigbee
network.  Work-directory locks remain evidence mirrors, not exclusion
authority.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
import socket
import tempfile

SCHEMA = 1
ACCEPTED_PHASE = 'postflash_accepted'
RECONCILED_PHASE = 'installed_image_reconciled'
SOFTWARE_VERIFIED_PHASE = 'postflash_software_verified_network_released'
SOURCE_UNCHANGED_PHASE = 'source_unchanged_reconciled'
ABANDONED_PHASE = 'source_unchanged_abandoned'
DEFERRED_RECOVERY_PHASE = 'ota_transfer_complete_recovery_deferred'
FAILED_POSTFLASH_PHASE = 'postflash_retention_failed_terminalized'


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def authority_path(network_lock_dir, network_id):
    if not (isinstance(network_id, str) and network_id.strip()):
        raise ValueError('Stable Zigbee network_id is required')
    root = Path(network_lock_dir).expanduser().resolve()
    key = hashlib.sha256(network_id.strip().encode('utf8')).hexdigest()[:32]
    return root / ('bseed-zigbee-' + key + '.json')



# Fail-closed, opt-in two-slot admission. The legacy network lease format and
# all existing per-device phase validations remain unchanged. Never migrate a
# running single-slot campaign in place.
_SLOT_RE = re.compile(r'^(bseed-zigbee-[0-9a-f]{32})[.]slot-(0x[0-9a-f]{16})[.]json$')
_IEEE_RE = re.compile(r'^0x[0-9a-f]{16}$')


def dual_slot_path(base, ieee):
    """Private per-IEEE lease in the same authoritative coordinator directory."""
    name = str(ieee).lower()
    if not _IEEE_RE.fullmatch(name):
        raise ValueError('Dual OTA requires a complete verified 0x IEEE')
    return Path(base).with_name(Path(base).stem + '.slot-' + name + '.json')


def _lease_group(path):
    """Return (network singleton path, slot IEEE or None) for this lease."""
    path = Path(path)
    match = _SLOT_RE.fullmatch(path.name)
    if match:
        return path.with_name(match.group(1) + '.json'), match.group(2)
    if re.fullmatch(r'bseed-zigbee-[0-9a-f]{32}[.]json', path.name):
        return path, None
    raise ValueError('Network lease path is not a known authoritative format')


@contextlib.contextmanager
def _coordinator_mutex(base):
    """Atomic same-filesystem directory gate; never auto-break a stale lock."""
    folder = Path(base).with_suffix('.admission-lock')
    for _ in range(80):
        try:
            os.mkdir(folder)
            break
        except FileExistsError:
            time.sleep(0.05)
    else:
        raise RuntimeError('Coordinator lease admission is busy or orphaned; fail closed')
    try:
        yield
    finally:
        os.rmdir(folder)


def _active_slots(base):
    result = []
    for slot in sorted(base.parent.glob(base.stem + '.slot-*.json')):
        owner = read_lock(slot)  # Invalid/truncated JSON must block admission.
        if owner is None or owner.get('parallel_ota_max') != 2:
            raise RuntimeError('Invalid two-slot lease record')
        if slot != dual_slot_path(base, owner.get('ieee')):
            raise RuntimeError('Two-slot lease filename/IEEE mismatch')
        result.append(owner)
    return result


def _validate_dual_peers(target_ieee, peers):
    if not isinstance(peers, list) or len(peers) != 1:
        raise ValueError('Exactly one reciprocal parallel OTA peer IEEE is required')
    normalized = str(peers[0]).lower()
    if not _IEEE_RE.fullmatch(normalized) or normalized == str(target_ieee).lower():
        raise ValueError('Parallel OTA peer must be a distinct complete IEEE')
    return [normalized]


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
    base = authority_path(root, network_id)
    slots = profile.get('parallel_ota_max', 1)
    if type(slots) is not int or slots not in (1, 2):
        raise ValueError('parallel_ota_max must be the integer 1 or 2')
    if slots == 1:
        return base
    ieee = str(profile.get('ieee') or '').lower()
    if not _IEEE_RE.fullmatch(ieee):
        raise ValueError('Two-slot campaign must pin the device IEEE')
    _validate_dual_peers(ieee, profile.get('concurrent_ota_ieees'))
    if profile.get('preflash_role') != profile.get('postflash_role'):
        raise ValueError('Two-slot pilot permits only same-role updates')
    if profile.get('force_test_transition') is True:
        raise ValueError('Two-slot pilot refuses FORCE/role transitions')
    if profile.get('source_rejoin_strategy') != 'none':
        raise ValueError('Two-slot pilot refuses any source-rejoin automation')
    if profile.get('allow_join_all_fallback', False) is not False:
        raise ValueError('Two-slot pilot refuses permit-join-all')
    if profile.get('block_bytes') != 32:
        raise ValueError('Two-slot initial pilot requires 32-byte OTA blocks')
    delay = profile.get('response_delay_ms')
    if type(delay) is not int or delay < 1800:
        raise ValueError('Two-slot initial pilot requires >=1800ms per-device response pacing')
    return dual_slot_path(base, ieee)


def read_lock(path):
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf8'))
    if not (isinstance(data, dict) and data.get('schema') == SCHEMA):
        raise ValueError('Invalid network lock record')
    return data


def ownership_diagnosis(record):
    """Read-only distinction between a network lease and hardware acceptance.

    A successful transport is not proof of retention, relay behaviour or
    device acceptance. This function **never** releases ownership, changes a
    campaign phase, or grants authorization for a subsequent OTA.
    """
    if record is None:
        return {
            'network_ownership': 'unclaimed',
            'transport_state': 'none',
            'hardware_acceptance': 'not_assessed',
            'release_authorized': False,
        }
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError('Invalid network ownership record')
    phase = record.get('phase')
    if phase in ('ota_transfer_ok_postflash_unverified', 'postflash_candidate'):
        transport = 'completed'
        acceptance = 'unverified'
        next_step = 'review_terminalization_and_preserve_failed_acceptance'
    elif phase == ACCEPTED_PHASE:
        transport = 'completed'
        acceptance = 'accepted_recorded'
        next_step = 'existing_explicit_accepted_release'
    elif phase == SOFTWARE_VERIFIED_PHASE:
        transport = 'completed'
        acceptance = 'not_hardware_accepted'
        next_step = 'software_verified_network_ownership_can_be_released'
    elif phase in (RECONCILED_PHASE, SOURCE_UNCHANGED_PHASE):
        transport = 'reconciled'
        acceptance = 'unverified'
        next_step = 'existing_evidence_backed_reconciled_release'
    elif phase == FAILED_POSTFLASH_PHASE:
        transport = 'completed'
        acceptance = 'failed_retention_recorded'
        next_step = 'failed_ota_accepted_as_failure; new candidate needs new preflight'
    elif phase == DEFERRED_RECOVERY_PHASE:
        transport = 'completed'
        acceptance = 'unverified'
        next_step = 'offline_target_recovery_deferred; no new OTA for this IEEE'
    elif phase == ABANDONED_PHASE:
        transport = 'abandoned_source_unchanged'
        acceptance = 'unverified'
        next_step = 'release_abandoned_campaign'
    elif phase in ('ota_running', 'preflight_complete'):
        transport = 'active_or_unknown'
        acceptance = 'not_assessed'
        next_step = 'retain_network_ownership'
    elif phase in ('update_error', 'update_timeout_or_unconfirmed',
                   'source_verified_candidate_pending'):
        transport = 'unconfirmed'
        acceptance = 'unverified'
        next_step = 'reconcile_source_or_installed_image'
    elif phase == 'preflight_abort':
        transport = 'not_started'
        acceptance = 'not_assessed'
        next_step = 'existing_preflight_abort_release'
    else:
        transport = 'unknown'
        acceptance = 'unverified'
        next_step = 'manual_investigation'
    return {
        'network_ownership': 'held',
        'owner_device': record.get('device'),
        'owner_ieee': record.get('ieee'),
        'phase': phase,
        'transport_state': transport,
        'hardware_acceptance': acceptance,
        'release_authorized': False,
        'next_step': next_step,
    }


def acquire(path, *, network_id, token, device, ieee, image_sha256,
            parallel_allowed_ieees=None):
    """Acquire a single or opt-in two-slot lease atomically per coordinator.

    In two-slot mode both participants must explicitly allowlist each other.
    A running legacy single-slot lease blocks new two-slot admission: we do
    not rewrite its ownership record or interfere with its live runner.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    base, slot_ieee = _lease_group(path)
    dual = slot_ieee is not None
    target = str(ieee).lower()
    if not _IEEE_RE.fullmatch(target):
        raise ValueError('Network lease requires a full target IEEE')
    if dual and target != slot_ieee:
        raise ValueError('Two-slot lease does not belong to requested IEEE')
    if not dual and parallel_allowed_ieees:
        raise ValueError('Legacy single-slot lease cannot allow concurrent peers')
    peers = _validate_dual_peers(target, parallel_allowed_ieees) if dual else []
    record = {
        'schema': SCHEMA,
        'network_id': network_id,
        'token': token,
        'device': device,
        'ieee': target,
        'image_sha256': image_sha256,
        'phase': 'preflight_complete',
        'owner_host': socket.gethostname(),
        'acquired_at': now_iso(),
        'updated_at': now_iso(),
    }
    if dual:
        record.update(parallel_ota_max=2, allowed_peer_ieees=peers)
    with _coordinator_mutex(base):
        slots = _active_slots(base)
        if dual:
            if base.exists():
                raise RuntimeError('Legacy network OTA ownership exists; two-slot admission refused')
            if len(slots) >= 2:
                raise RuntimeError('Both parallel OTA coordinator slots are occupied')
            if any(x['ieee'] == target for x in slots):
                raise RuntimeError('Target IEEE already owns a parallel OTA slot')
            if slots:
                peer = slots[0]
                if (peer.get('network_id') != network_id or
                    peer['ieee'] != peers[0] or
                    peer.get('allowed_peer_ieees') != [target]):
                    raise RuntimeError('Two-slot campaigns require reciprocal exact-IEEE peer allowlists')
                if peer.get('phase') != 'ota_running':
                    raise RuntimeError('Second OTA slot requires its reciprocal peer to be actively updating')
        else:
            if slots:
                raise RuntimeError('Two-slot OTA ownership exists; legacy single-slot admission refused')
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
    # Serialize updates with acquire/release; otherwise a stale os.replace()
    # could resurrect a terminal lease removed by the release transaction.
    path = Path(path)
    base, _ = _lease_group(path)
    with _coordinator_mutex(base):
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
    base, _ = _lease_group(path)
    with _coordinator_mutex(base):
        current = read_lock(path)
        if current is None or current.get('token') != token or current.get('phase') != allowed_phase:
            raise RuntimeError('Network owner or terminal phase changed before release')
        Path(path).unlink()
    return True


def release_preflight_abort(path, token):
    return _release_phase(path, token, 'preflight_abort')


def release_accepted(path, token):
    return _release_phase(path, token, ACCEPTED_PHASE)


def release_reconciled(path, token):
    """Release network ownership after exact installed-image reconciliation.

    This is intentionally distinct from hardware/postflash acceptance and may
    only release a lock already moved to RECONCILED_PHASE by the dedicated
    evidence validator.
    """
    return _release_phase(path, token, RECONCILED_PHASE)


def release_source_unchanged(path, token):
    """Release ownership only after fresh proof that the preflash image/role
    is still installed and the attempted OTA is no longer active.

    This is not transport success and not hardware acceptance.
    """
    return _release_phase(path, token, SOURCE_UNCHANGED_PHASE)


def release_abandoned(path, token):
    """Release only a failed campaign explicitly terminalized after fresh
    proof that its original source image/role is still installed and no OTA
    is active. This is not acceptance and never authorizes reuse of the old
    campaign workdir.
    """
    return _release_phase(path, token, ABANDONED_PHASE)


def release_software_verified(path, token):
    """Release the shared lease ONLY after dedicated exact-device software
    evidence validation has set SOFTWARE_VERIFIED_PHASE. This is NOT hardware
    acceptance and cannot be invoked from a mere OTA transport-success phase.
    """
    return _release_phase(path, token, SOFTWARE_VERIFIED_PHASE)


def release_failed_postflash(path, token):
    """Release only a terminal, evidence-backed FAILED PM acceptance phase.

    The validator must already have set FAILED_POSTFLASH_PHASE on the exact
    matching work and shared records. Never imply a successful flash.
    """
    return _release_phase(path, token, FAILED_POSTFLASH_PHASE)


def release_deferred_recovery(path, token):
    """Release a completed-transfer lease after separately verified deferred recovery.

    This never accepts the target image or clears its per-device campaign lock.
    Only the exact dedicated terminalization validator may set the phase.
    """
    return _release_phase(path, token, DEFERRED_RECOVERY_PHASE)
