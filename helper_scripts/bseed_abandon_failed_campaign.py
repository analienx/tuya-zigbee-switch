"""Terminalize a failed BSEED OTA campaign after exact source proof.

This operation is intentionally different from retry/reconciliation success.
It frees the network-wide OTA lease only after a fresh source-only
reconciliation proves that the exact preflash build/role/IEEE is still present,
no OTA is active during the quiet window, and the original campaign authority
(token/device/IEEE/SHA) is intact.

The old workdir remains terminal in source_unchanged_abandoned and is NOT a
valid starting point for another flash. A future attempt must use a new campaign
and fresh preflight.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import uuid

from bseed_network_campaign_lock import (
    ABANDONED_PHASE,
    read_lock,
    release_abandoned,
    update,
)
from bseed_ota_campaign import load_profile, network_lock_path

PENDING_PHASE = 'source_verified_candidate_pending'
ELIGIBLE_START_PHASES = {
    'update_error',
    'update_timeout_or_unconfirmed',
    PENDING_PHASE,
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def validate_pending(profile: dict) -> tuple[Path, dict, Path, dict, Path, dict]:
    work = Path(profile['workdir'])
    work_lock_path = work / 'ACTIVE_LOCK.json'
    if not work_lock_path.is_file():
        raise RuntimeError('Campaign work lock missing')
    work_lock = json.loads(work_lock_path.read_text(encoding='utf8'))
    if (work_lock.get('device'), work_lock.get('ieee'), work_lock.get('sha256'),
            work_lock.get('phase')) != (
            profile['device'], profile['ieee'], profile['sha256'], PENDING_PHASE):
        raise RuntimeError('Exact candidate-pending work authority required')

    token = work_lock.get('token')
    if not isinstance(token, str) or not token:
        raise RuntimeError('Campaign token missing')

    network_path = network_lock_path(profile, required=True)
    network = read_lock(network_path)
    if not network or (
        network.get('token'), network.get('device'), network.get('ieee'),
        network.get('image_sha256'), network.get('phase')
    ) != (
        token, profile['device'], profile['ieee'], profile['sha256'], PENDING_PHASE
    ):
        raise RuntimeError('Exact candidate-pending network authority required')

    raw = work_lock.get('reconciliation_evidence')
    evidence_path = Path(raw) if raw else None
    if (not evidence_path or evidence_path.resolve().parent != work.resolve()
            or not evidence_path.name.startswith('source_unchanged_')
            or not evidence_path.is_file()):
        raise RuntimeError('Fresh source reconciliation evidence missing')
    evidence = json.loads(evidence_path.read_text(encoding='utf8'))
    if (
        evidence.get('result'), evidence.get('ieee'), evidence.get('device'),
        evidence.get('candidate_sha256'), evidence.get('source_build'),
        evidence.get('source_role'), evidence.get('fresh_get_ieee_verified'),
        evidence.get('fresh_get_build_role_verified'),
        evidence.get('candidate_verification_deferred'),
        evidence.get('ota_transport_success'), evidence.get('hardware_acceptance')
    ) != (
        PENDING_PHASE, profile['ieee'], profile['device'], profile['sha256'],
        profile['preflash_build'], profile['preflash_role'], True, True, True,
        False, False
    ):
        raise RuntimeError('Source evidence cannot authorize abandonment')
    return work_lock_path, work_lock, network_path, network, evidence_path, evidence


def finalize_abandonment(profile: dict, *, reason: str) -> Path:
    work_lock_path, work_lock, network_path, network, source_path, source = (
        validate_pending(profile)
    )
    token = work_lock['token']

    # PM quarantine can be released only for the freshly proven unchanged source.
    # This does not accept the failed candidate.
    if profile.get('require_pm') is True:
        from bseed_ota_resume_supervisor import restore_pm_source_telemetry
        restore_pm_source_telemetry(profile, Path(profile['workdir']))

    abandoned_at = now()
    evidence = {
        'result': ABANDONED_PHASE,
        'at': abandoned_at,
        'device': profile['device'],
        'ieee': profile['ieee'],
        'candidate_sha256': profile['sha256'],
        'source_build': profile['preflash_build'],
        'source_role': profile['preflash_role'],
        'source_reconciliation_evidence': str(source_path),
        'source_reconciled_at': source['at'],
        'reason': reason,
        'ota_transport_success': False,
        'hardware_acceptance': False,
        'candidate_retry_authorized': False,
        'old_campaign_reusable': False,
        'network_release': True,
    }
    evidence_path = Path(profile['workdir']) / (
        'source_abandoned_' + uuid.uuid4().hex + '.json'
    )
    with evidence_path.open('x', encoding='utf8') as handle:
        json.dump(evidence, handle, indent=2)
        handle.write('\n')

    updated_work = dict(work_lock)
    updated_work.update(
        phase=ABANDONED_PHASE,
        abandoned_at=abandoned_at,
        abandonment_reason=reason,
        abandonment_evidence=str(evidence_path),
        hardware_acceptance=False,
        candidate_retry_authorized=False,
    )
    atomic_json(work_lock_path, updated_work)

    update(
        network_path, token, ABANDONED_PHASE,
        abandoned_at=abandoned_at,
        abandonment_reason=reason,
        abandonment_evidence=str(evidence_path),
        hardware_acceptance=False,
        candidate_retry_authorized=False,
    )
    release_abandoned(network_path, token)
    return evidence_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True)
    p.add_argument('--confirm-ieee', required=True)
    p.add_argument('--confirm-sha256', required=True)
    p.add_argument(
        '--reason',
        choices=('candidate_unresponsive', 'operator_abandoned'),
        required=True,
    )
    args = p.parse_args(argv)
    profile = load_profile(args.profile)
    if args.confirm_ieee != profile['ieee']:
        raise ValueError('Exact IEEE confirmation mismatch')
    if args.confirm_sha256 != profile['sha256']:
        raise ValueError('Exact candidate SHA-256 confirmation mismatch')

    work_lock_path = Path(profile['workdir']) / 'ACTIVE_LOCK.json'
    if not work_lock_path.is_file():
        raise RuntimeError('Failed campaign work lock missing')
    phase = json.loads(work_lock_path.read_text(encoding='utf8')).get('phase')
    if phase not in ELIGIBLE_START_PHASES:
        raise RuntimeError('Campaign phase is not eligible for abandonment')

    if phase != PENDING_PHASE:
        from bseed_ota_source_reconcile import reconcile
        result = reconcile(
            args.profile,
            args.confirm_ieee,
            45,
            verify_candidate=False,
        )
        if result.get('result') != PENDING_PHASE:
            raise RuntimeError('Source-only reconciliation did not reach pending state')

    evidence = finalize_abandonment(profile, reason=args.reason)
    print('FAILED_CAMPAIGN_ABANDONED_NETWORK_RELEASED', profile['ieee'], flush=True)
    print('PRIVATE_EVIDENCE', evidence, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
