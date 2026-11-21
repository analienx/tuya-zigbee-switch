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
    # The legacy single-owner API also accepts operator/test-owned *.json
    # paths (e.g. network-lock.json); it always has. Preserve that contract.
    # Strict canonical coordinator IDs are enforced at profile resolution,
    # and ONLY canonical names can enter the new parallel-slot mode.
    if path.suffix == '.json' and '.slot-' not in path.stem:
        return path, None
    raise ValueError('Network lease path is not a valid singleton or canonical dual-slot path')


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
        except PermissionError:
            # Windows can transiently deny mkdir while another owner removes
            # the same admission directory. Retry only that exact race.
            if os.name != 'nt' or not folder.is_dir():
                raise
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
        if owner is None or owner.get('parallel_ota_max') not in (2, 3):
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



def _validate_three_peers(target_ieee, peers):
    """Future 3-slot policy: all currently active peer IEEEs, max two."""
    if not isinstance(peers, list) or len(peers) > 2:
        raise ValueError('Three-slot OTA requires a 0..2 IEEE peer allowlist')
    normalized = [str(value).lower() for value in peers]
    if (len(normalized) != len(set(normalized)) or
            any(not _IEEE_RE.fullmatch(i) or i == target_ieee for i in normalized)):
        raise ValueError('Three-slot OTA needs distinct valid non-target peer IEEEs')
    return normalized


def _validate_three_slot_admission(base, slots, record, *, peers,
                                   legacy_peer_work_lock=None,
                                   active_peer_work_lock=None,
                                   peer_work_locks=None):
    """Under coordinator mutex. Verify immutable existing campaigns before 3rd admission.

    Does not modify or release either running campaign; their lock files and
    transport parameters remain identical to what their runner started with.
    """
    legacy = read_lock(base)
    owners = ([(legacy, base)] if legacy is not None else []) + [
        (slot, dual_slot_path(base, slot['ieee'])) for slot in slots
    ]
    if len(owners) >= 3:
        raise RuntimeError('Three OTA coordinator slots are occupied')
    if set(peers) != {owner['ieee'] for owner, _ in owners}:
        raise RuntimeError('Exact active three-slot peer allowlist required')
    mappings = dict(peer_work_locks or {})
    if legacy_peer_work_lock and legacy:
        mappings[legacy['ieee']] = legacy_peer_work_lock
    if active_peer_work_lock and len(slots) == 1:
        mappings[slots[0]['ieee']] = active_peer_work_lock
    for owner, lease_path in owners:
        ieee = owner['ieee']
        if (owner.get('network_id') != record['network_id']
            or owner.get('phase') != 'ota_running'
            or ieee == record['ieee']):
            raise RuntimeError('Three-slot peer is not an independent active OTA')
        path_text = mappings.get(ieee) or owner.get('work_lock_path')
        if not path_text:
            raise RuntimeError('Three-slot peer exact work-lock path required: ' + ieee)
        path = Path(path_text).expanduser().resolve()
        if path.name != 'ACTIVE_LOCK.json' or not path.is_file():
            raise RuntimeError('Three-slot peer has no authoritative work lock: ' + ieee)
        work = json.loads(path.read_text(encoding='utf8'))
        if (work.get('ieee'), work.get('token'), work.get('sha256'),
            work.get('phase'), Path(work.get('network_lock') or '').resolve()) != (
            ieee, owner['token'], owner['image_sha256'], 'ota_running',
            lease_path.resolve()):
            raise RuntimeError('Three-slot peer work/lease identity mismatch: ' + ieee)
        status_path = path.parent / 'LIVE_STATUS.json'
        live = json.loads(status_path.read_text(encoding='utf8'))
        at = dt.datetime.fromisoformat(live['observed_at'])
        if at.tzinfo is None:
            raise RuntimeError('Three-slot peer status timestamp has no timezone')
        age = (dt.datetime.now(dt.timezone.utc)
               - at.astimezone(dt.timezone.utc)).total_seconds()
        progress = (live.get('update') or {}).get('progress')
        if not (0 <= age <= 120
                and live.get('ieee') == ieee and live.get('token') == owner['token']
                and live.get('phase') == 'ota_running'
                and (live.get('update') or {}).get('state') == 'updating'
                and type(progress) in (int, float) and 0 <= progress < 100):
            raise RuntimeError('Three-slot peer is not freshly updating: ' + ieee)
    record.update(
        parallel_ota_max=3,
        allowed_peer_ieees=peers,
        admission_peer_tokens={owner['ieee']:owner['token'] for owner,_ in owners},
        active_slots_at_admission=len(owners)+1,
    )
    return True


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
    if type(slots) is not int or slots not in (1, 2, 3):
        raise ValueError('parallel_ota_max must be the integer 1, 2, or 3')
    if slots == 1:
        return base
    ieee = str(profile.get('ieee') or '').lower()
    if not _IEEE_RE.fullmatch(ieee):
        raise ValueError('Two-slot campaign must pin the device IEEE')
    (_validate_three_peers(ieee, profile.get('concurrent_ota_ieees') or [])
     if slots == 3 else _validate_dual_peers(ieee, profile.get('concurrent_ota_ieees')))
    if profile.get('preflash_role') != profile.get('postflash_role'):
        raise ValueError('Two-slot pilot permits only same-role updates')
    if profile.get('force_test_transition') is True:
        raise ValueError('Two-slot pilot refuses FORCE/role transitions')
    if profile.get('source_rejoin_strategy') != 'none':
        raise ValueError('Two-slot pilot refuses any source-rejoin automation')
    if profile.get('allow_join_all_fallback', False) is not False:
        raise ValueError('Two-slot pilot refuses permit-join-all')
    if profile.get('block_bytes') not in (32, 48) or isinstance(profile.get('block_bytes'), bool):
        raise ValueError('Two-slot pilot requires 32- or 48-byte OTA blocks')
    from bseed_ota_block_policy import check_block_bytes
    check_block_bytes(profile, allowed=(32, 48))
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
            parallel_allowed_ieees=None, legacy_peer_work_lock=None,
            active_peer_work_lock=None, completed_peer_work_lock=None,
            parallel_ota_max=None, peer_work_locks=None, work_lock_path=None):
    """Acquire a single or opt-in two-slot lease atomically per coordinator.

    In two-slot mode both participants must explicitly allowlist each other.
    A running legacy single-slot lease blocks new two-slot admission: we do
    not rewrite its ownership record or interfere with its live runner.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    base, slot_ieee = _lease_group(path)
    dual = slot_ieee is not None
    triple = dual and parallel_ota_max == 3
    if parallel_ota_max not in (None, 1, 2, 3):
        raise ValueError('Invalid parallel_ota_max at lease acquisition')
    if not dual and parallel_ota_max == 3:
        raise ValueError('Three-slot admission must use a per-IEEE slot')
    target = str(ieee).lower()
    if not _IEEE_RE.fullmatch(target):
        raise ValueError('Network lease requires a full target IEEE')
    if dual and target != slot_ieee:
        raise ValueError('Two-slot lease does not belong to requested IEEE')
    if not dual and parallel_allowed_ieees:
        raise ValueError('Legacy single-slot lease cannot allow concurrent peers')
    peers = (_validate_three_peers(target, parallel_allowed_ieees or []) if triple
             else _validate_dual_peers(target, parallel_allowed_ieees) if dual else [])
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
        record.update(parallel_ota_max=3 if triple else 2, allowed_peer_ieees=peers)
        if triple:
            if not work_lock_path or Path(work_lock_path).name != 'ACTIVE_LOCK.json':
                raise ValueError('Three-slot owner must pin its work lock')
            record['work_lock_path'] = str(Path(work_lock_path).resolve())
    with _coordinator_mutex(base):
        slots = _active_slots(base)
        if triple:
            _validate_three_slot_admission(
                base, slots, record, peers=peers,
                legacy_peer_work_lock=legacy_peer_work_lock,
                active_peer_work_lock=active_peer_work_lock,
                peer_work_locks=peer_work_locks)
        elif dual:
            chained = False
            if completed_peer_work_lock:
                # The operator permits the NEXT active OTA while keeping both
                # successful but hardware-unaccepted transactions immutable.
                # There may be three slot records, but only TWO are active.
                # It requires the exact historical chain:
                #   legacy finished -> first peer finished -> second peer active
                #   -> this one starts. Every token/IEEE/OTA hash is pinned.
                if not base.exists() or len(slots) != 2 or not legacy_peer_work_lock or not active_peer_work_lock:
                    raise RuntimeError('Verified completed+active succession chain not present')
                legacy = read_lock(base)
                done = [r for r in slots if r.get('phase') == 'ota_transfer_ok_postflash_unverified']
                running = [r for r in slots if r.get('phase') == 'ota_running']
                if len(done)!=1 or len(running)!=1:
                    raise RuntimeError('Requires exactly one completed and one actively updating peer')
                done,running=done[0],running[0]
                if not (legacy.get('network_id')==network_id and
                        legacy.get('phase')=='ota_transfer_ok_postflash_unverified' and
                        done.get('network_id')==network_id and
                        running.get('network_id')==network_id and
                        running.get('ieee')==peers[0] and
                        done.get('mixed_legacy_peer_ieee')==legacy.get('ieee') and
                        done.get('mixed_legacy_peer_token')==legacy.get('token') and
                        running.get('mixed_completed_legacy_ieee')==legacy.get('ieee') and
                        running.get('mixed_completed_legacy_token')==legacy.get('token') and
                        running.get('mixed_active_peer_ieee')==done.get('ieee') and
                        running.get('mixed_active_peer_token')==done.get('token') and
                        done.get('allowed_peer_ieees')==[legacy['ieee']] and
                        running.get('allowed_peer_ieees')==[done['ieee']]):
                    raise RuntimeError('Exact earlier-transaction chain does not match')
                checks = [
                    (legacy, Path(legacy_peer_work_lock), base),
                    (done, Path(completed_peer_work_lock), dual_slot_path(base, done['ieee'])),
                    (running, Path(active_peer_work_lock), dual_slot_path(base, running['ieee']))]
                for owner, work_path, expected_lease in checks:
                    work_path=work_path.expanduser().resolve()
                    if work_path.name != 'ACTIVE_LOCK.json' or not work_path.is_file():
                        raise RuntimeError('One historical campaign lock missing')
                    work=json.loads(work_path.read_text(encoding='utf8'))
                    expected_phase=owner['phase']
                    if (work.get('ieee'),work.get('token'),work.get('sha256'),
                        work.get('phase'),Path(work.get('network_lock') or '').resolve()) != (
                            owner['ieee'],owner['token'],owner['image_sha256'],
                            expected_phase,expected_lease.resolve()):
                        raise RuntimeError('Local and shared campaign ownership differ')
                    if expected_phase=='ota_transfer_ok_postflash_unverified':
                        reply=work.get('response') or {}
                        last=json.loads((work_path.parent/'LIVE_STATUS.json').read_text(encoding='utf8'))
                        if (reply.get('status')!='ok' or
                            reply.get('transaction')!=owner['token'] or
                            (reply.get('data') or {}).get('id')!=owner['ieee'] or
                            last.get('token')!=owner['token'] or
                            last.get('phase')!=expected_phase or
                            (last.get('update') or {}).get('state')!='idle'):
                            raise RuntimeError('Historical transport not provably complete/idle')
                    else:
                        last=json.loads((work_path.parent/'LIVE_STATUS.json').read_text(encoding='utf8'))
                        at=dt.datetime.fromisoformat(last['observed_at'])
                        if at.tzinfo is None:
                            raise RuntimeError('Active peer status timestamp lacks timezone')
                        age=(dt.datetime.now(dt.timezone.utc)-at.astimezone(dt.timezone.utc)).total_seconds()
                        progress=(last.get('update') or {}).get('progress')
                        if not (0<=age<=120 and last.get('ieee')==owner['ieee'] and
                                last.get('token')==owner['token'] and
                                last.get('phase')=='ota_running' and
                                (last.get('update') or {}).get('state')=='updating' and
                                type(progress) in (int,float) and 0<=progress<100):
                            raise RuntimeError('Live OTA peer is not freshly progressing')
                record.update(chain_legacy_ieee=legacy['ieee'],
                              chain_legacy_token=legacy['token'],
                              chain_completed_ieee=done['ieee'],
                              chain_completed_token=done['token'],
                              chain_active_ieee=running['ieee'],
                              chain_active_token=running['token'])
                chained=True
            elif base.exists():
                # Keep the original singleton immutable. One previously
                # completed singleton plus TWO verified live dual-slot peers
                # still means at most two simultaneous OTA transfers.
                if not legacy_peer_work_lock:
                    raise RuntimeError('Legacy network OTA ownership exists; two-slot admission refused')
                if len(slots) > 1:
                    raise RuntimeError('Legacy mixed mode has no free additional slot')
                owner = read_lock(base)
                peer_path = Path(legacy_peer_work_lock).expanduser().resolve()
                if peer_path.name != 'ACTIVE_LOCK.json' or not peer_path.is_file():
                    raise RuntimeError('Exact legacy peer work lock missing')
                peer_lock = json.loads(peer_path.read_text(encoding='utf8'))
                expected_legacy_phase = ('ota_transfer_ok_postflash_unverified'
                                         if slots else 'ota_running')
                if (owner.get('network_id'), owner.get('ieee'), owner.get('phase'),
                    owner.get('image_sha256')) != (
                        network_id, peer_lock.get('ieee'), expected_legacy_phase, image_sha256):
                    raise RuntimeError('Legacy owner network/image/phase mismatch')
                if (peer_lock.get('ieee'), peer_lock.get('token'), peer_lock.get('sha256'),
                    peer_lock.get('phase'), Path(peer_lock.get('network_lock') or '').resolve()) != (
                        owner['ieee'], owner['token'], image_sha256, expected_legacy_phase, base.resolve()):
                    raise RuntimeError('Legacy work lock does not match intact network lease')
                progress_file = peer_path.parent / 'LIVE_STATUS.json'
                live = json.loads(progress_file.read_text(encoding='utf8'))
                if live.get('ieee') != owner['ieee'] or live.get('token') != owner['token']:
                    raise RuntimeError('Legacy status identity mismatch')
                if slots:
                    if not active_peer_work_lock:
                        raise RuntimeError('Active peer work lock required for mixed completed+active mode')
                    first = slots[0]
                    if (first.get('network_id'), first.get('ieee'), first.get('phase'),
                        first.get('image_sha256'), first.get('mixed_legacy_peer_ieee'),
                        first.get('mixed_legacy_peer_token'),
                        first.get('allowed_peer_ieees')) != (
                            network_id, peers[0], 'ota_running', image_sha256,
                            owner['ieee'], owner['token'], [owner['ieee']]):
                        raise RuntimeError('Mixed active peer not traceable to completed legacy owner')
                    reply = peer_lock.get('response') or {}
                    if (reply.get('status') != 'ok'
                        or reply.get('transaction') != owner['token']
                        or (reply.get('data') or {}).get('id') != owner['ieee']
                        or live.get('phase') != 'ota_transfer_ok_postflash_unverified'
                        or (live.get('update') or {}).get('state') != 'idle'
                        or (live.get('update') or {}).get('installed_version') !=
                           (live.get('update') or {}).get('latest_version')):
                        raise RuntimeError('Completed legacy transport and idle status are not proven')
                    active_path=Path(active_peer_work_lock).expanduser().resolve()
                    if active_path.name != 'ACTIVE_LOCK.json' or not active_path.is_file():
                        raise RuntimeError('Active peer campaign lock missing')
                    active=json.loads(active_path.read_text(encoding='utf8'))
                    if (active.get('ieee'),active.get('token'),active.get('sha256'),
                        active.get('phase'),Path(active.get('network_lock') or '').resolve()) != (
                            first['ieee'],first['token'],image_sha256,
                            'ota_running',dual_slot_path(base,first['ieee']).resolve()):
                        raise RuntimeError('Active peer local ownership mismatch')
                    active_live=json.loads((active_path.parent / 'LIVE_STATUS.json').read_text(encoding='utf8'))
                    observed_at=dt.datetime.fromisoformat(active_live['observed_at'])
                    if observed_at.tzinfo is None:
                        raise RuntimeError('Active peer status must have timezone')
                    age=(dt.datetime.now(dt.timezone.utc)-observed_at.astimezone(dt.timezone.utc)).total_seconds()
                    progress=(active_live.get('update') or {}).get('progress')
                    if not (0<=age<=120 and active_live.get('ieee')==first['ieee']
                            and active_live.get('token')==first['token']
                            and active_live.get('phase')=='ota_running'
                            and (active_live.get('update') or {}).get('state')=='updating'
                            and type(progress) in (int,float) and 0<=progress<100):
                        raise RuntimeError('Active peer not freshly updating')
                    chained=True
                    record.update(mixed_completed_legacy_ieee=owner['ieee'],
                                  mixed_completed_legacy_token=owner['token'],
                                  mixed_active_peer_ieee=first['ieee'],
                                  mixed_active_peer_token=first['token'])
                else:
                    observed_at=dt.datetime.fromisoformat(live['observed_at'])
                    if observed_at.tzinfo is None:
                        raise RuntimeError('Legacy peer status timestamp not timezone-aware')
                    age=(dt.datetime.now(dt.timezone.utc)-
                         observed_at.astimezone(dt.timezone.utc)).total_seconds()
                    progress=(live.get('update') or {}).get('progress')
                    if not (owner['ieee']==peers[0] and 0<=age<=120
                            and live.get('phase')=='ota_running'
                            and (live.get('update') or {}).get('state')=='updating'
                            and type(progress) in (int,float) and 0<=progress<100):
                        raise RuntimeError('Legacy peer not freshly updating')
                    record.update(mixed_legacy_peer_ieee=owner['ieee'],
                                  mixed_legacy_peer_token=owner['token'],
                                  mixed_legacy_peer_lock=str(peer_path))
            elif legacy_peer_work_lock or active_peer_work_lock:
                raise RuntimeError('Explicit mixed mode requires existing legacy singleton')
            if len(slots)>=2 and not completed_peer_work_lock:
                raise RuntimeError('Both parallel coordinator slots occupied')
            if any(x['ieee']==target for x in slots):
                raise RuntimeError('Target IEEE already owns a parallel OTA slot')
            if slots and not chained:
                peer=slots[0]
                if (peer.get('ieee') != peers[0] or
                        peer.get('allowed_peer_ieees') != [target]):
                    raise RuntimeError('Two-slot requires reciprocal exact active peer')
                if peer.get('phase') != 'ota_running':
                    raise RuntimeError('Second slot requires peer actively updating')
                if (peer.get('network_id')!=network_id or peer['ieee']!=peers[0]
                    or peer.get('allowed_peer_ieees')!=[target]
                    or peer.get('phase')!='ota_running'):
                    raise RuntimeError('Two-slot requires reciprocal exact active peer')
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
