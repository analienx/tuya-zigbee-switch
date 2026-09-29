"""Close network ownership after a timed-out OTA whose installed image is independently proven.

This helper never claims OTA transport success or hardware/release acceptance.
It validates private evidence from the exact campaign, records the reconciled
installed-image state in the work lock, then releases only network ownership so
another explicitly authorized campaign may start.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from bseed_network_campaign_lock import (
    RECONCILED_PHASE, read_lock, release_reconciled, update,
)
from bseed_ota_campaign import ROOT, load_profile, network_lock_path


EXPECTED_TIMEOUT_ISSUE = (
    'ValueError: Exact campaign identity/hash and successful OTA transport lock required'
)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_private(path):
    p = Path(path).expanduser().resolve()
    if p.is_relative_to(ROOT) or not p.is_file():
        raise ValueError('Evidence must be an existing private file outside repository')
    return p, json.loads(p.read_text(encoding='utf8'))


def validate_metadata(profile, evidence):
    if evidence.get('result') not in ('metadata_refreshed', 'metadata_already_correct'):
        raise ValueError('Fresh metadata reconciliation evidence required')
    if evidence.get('error') is not None or evidence.get('interview_ok') is not True:
        raise ValueError('Metadata reconciliation reported an error or missing interview')
    if evidence.get('fresh_inventory_observed') is not True:
        raise ValueError('Metadata reconciliation lacks fresh nonretained inventory')
    if evidence.get('ieee') != profile['ieee'] or evidence.get('device') != profile['device']:
        raise ValueError('Metadata evidence belongs to another target')
    live = evidence.get('live_zdo_after') or {}
    after = evidence.get('after') or {}
    if live.get('role') != profile['postflash_role']:
        raise ValueError('Metadata evidence lacks final expected live role')
    if (after.get('ieee_address'), after.get('software_build_id'), after.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('Metadata evidence does not prove exact installed image identity')


def validate_pm_audit(profile, evidence):
    if profile.get('require_pm') is not True:
        return
    if (evidence.get('ieee'), evidence.get('device'), evidence.get('expected_role'),
            evidence.get('expected_build')) != (
            profile['ieee'], profile['device'], profile['postflash_role'],
            profile['postflash_build']):
        raise ValueError('PM audit belongs to another target/build/role')
    if evidence.get('result') != 'read_only_audit_candidate' or evidence.get('issues'):
        raise ValueError('Passing read-only PM audit required')
    if evidence.get('writes') != 0 or evidence.get('missing_reporting'):
        raise ValueError('PM audit must be read-only with no reporting gaps')
    mqtt = evidence.get('mqtt') or {}
    if int(mqtt.get('messages') or 0) < 2 or float(mqtt.get('span_seconds') or 0) < 8:
        raise ValueError('PM audit lacks separated unsolicited MQTT evidence')


def validate_postflash(profile, evidence, work_lock):
    if (evidence.get('target_ieee'), evidence.get('target_name'),
            evidence.get('expected_role'), evidence.get('expected_build')) != (
            profile['ieee'], profile['device'], profile['postflash_role'],
            profile['postflash_build']):
        raise ValueError('Postflash evidence belongs to another target/build/role')
    issues = evidence.get('issues') or []
    if issues != [EXPECTED_TIMEOUT_ISSUE]:
        raise ValueError('Postflash evidence has issues beyond the known transport-timeout lock')
    if (evidence.get('pm_readiness') or {}).get('issues'):
        raise ValueError('PM readiness still has unresolved issues')
    if evidence.get('fresh_mqtt_state_observed') is not True or evidence.get('errors'):
        raise ValueError('Postflash evidence lacks fresh clean device state')
    inventory = evidence.get('inventory') or {}
    if (inventory.get('ieee_address'), inventory.get('software_build_id'),
            inventory.get('type'), inventory.get('interview_completed')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role'], True):
        raise ValueError('Postflash inventory does not prove exact installed identity')
    state = evidence.get('state') or {}
    previous = (work_lock.get('preflash_state') or {}).get('state_relay')
    if previous is not None and state.get('state_relay') != previous:
        raise ValueError('Physical relay state differs from preflash baseline')


def validate_guard_archive(profile, work_lock):
    if profile.get('require_pm') is not True:
        return None
    work = Path(profile['workdir'])
    if (work / 'PM_TELEMETRY_GUARD.json').exists():
        raise ValueError('PM telemetry quarantine is still active')
    token = work_lock.get('token')
    matches = list(work.glob('PM_TELEMETRY_RELEASED_RECONCILED_' + str(token) + '.json'))
    if len(matches) != 1:
        raise ValueError('Exact reconciled telemetry-release record required')
    record = json.loads(matches[0].read_text(encoding='utf8'))
    if (record.get('ieee'), record.get('sha256'), record.get('transport_phase_preserved')) != (
            profile['ieee'], profile['sha256'], 'update_timeout_or_unconfirmed'):
        raise ValueError('Telemetry-release record does not match timed-out campaign')
    return str(matches[0])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True)
    p.add_argument('--confirm-ieee', required=True)
    p.add_argument('--metadata-evidence', required=True)
    p.add_argument('--pm-audit-evidence', required=True)
    p.add_argument('--postflash-evidence', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()

    profile = load_profile(a.profile)
    if a.confirm_ieee != profile['ieee']:
        raise ValueError('Exact IEEE confirmation required')
    if profile['preflash_role'] != profile['postflash_role']:
        raise ValueError('Reconciled closure is only for a same-role timed-out campaign')
    image = Path(profile['image']).read_bytes()
    if hashlib.sha256(image).hexdigest() != profile['sha256']:
        raise ValueError('Profile image bytes no longer match timed-out campaign hash')

    work = Path(profile['workdir']).resolve()
    work_lock_path = work / 'ACTIVE_LOCK.json'
    work_lock = json.loads(work_lock_path.read_text(encoding='utf8'))
    if (work_lock.get('device'), work_lock.get('ieee'), work_lock.get('sha256'),
            work_lock.get('phase')) != (
            profile['device'], profile['ieee'], profile['sha256'],
            'update_timeout_or_unconfirmed'):
        raise ValueError('Exact timed-out work lock required')
    token = work_lock.get('token')
    if not token:
        raise ValueError('Timed-out campaign lock lacks token')

    network_path = network_lock_path(profile, required=True)
    network = read_lock(network_path)
    if not network or (network.get('token'), network.get('device'), network.get('ieee'),
            network.get('image_sha256'), network.get('phase')) != (
            token, profile['device'], profile['ieee'], profile['sha256'],
            'update_timeout_or_unconfirmed'):
        raise ValueError('Exact timed-out network lock required')

    metadata_path, metadata = load_private(a.metadata_evidence)
    audit_path, audit = load_private(a.pm_audit_evidence)
    postflash_path, postflash = load_private(a.postflash_evidence)
    validate_metadata(profile, metadata)
    validate_pm_audit(profile, audit)
    validate_postflash(profile, postflash, work_lock)
    guard_archive = validate_guard_archive(profile, work_lock)

    output = Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Use a fresh private output outside repository')

    record = {
        'at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'device': profile['device'],
        'ieee': profile['ieee'],
        'image_sha256': profile['sha256'],
        'installed_build': profile['postflash_build'],
        'installed_role': profile['postflash_role'],
        'result': RECONCILED_PHASE,
        'otaTransportSuccess': False,
        'hardwareAcceptance': False,
        'physicalAcceptance': False,
        'networkOwnershipReleased': False,
        'evidence': {
            'metadata': {'path': str(metadata_path), 'sha256': sha256_file(metadata_path)},
            'pmAudit': {'path': str(audit_path), 'sha256': sha256_file(audit_path)},
            'postflash': {'path': str(postflash_path), 'sha256': sha256_file(postflash_path)},
            'telemetryRelease': guard_archive,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2), encoding='utf8')

    work_lock.update({
        'phase': RECONCILED_PHASE,
        'reconciled_at': record['at'],
        'ota_transport_success': False,
        'hardware_acceptance': False,
        'reconciliation_evidence': str(output),
    })
    work_lock_path.write_text(json.dumps(work_lock, indent=2), encoding='utf8')
    update(network_path, token, RECONCILED_PHASE,
           reconciled_at=record['at'], ota_transport_success=False,
           hardware_acceptance=False, reconciliation_evidence=str(output))
    release_reconciled(network_path, token)
    record['networkOwnershipReleased'] = True
    output.write_text(json.dumps(record, indent=2), encoding='utf8')

    print(json.dumps(record, indent=2))
    print('NETWORK_OWNERSHIP_RELEASED_AFTER_INSTALLED_IMAGE_RECONCILIATION',
          profile['ieee'], flush=True)


if __name__ == '__main__':
    main()
