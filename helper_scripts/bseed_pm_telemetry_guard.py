"""Quarantine PM decoding across OTA until fresh, campaign-matched identity evidence.

Called by the canonical campaign only. Failures leave telemetry suppressed;
they never imply a successful update and never permit an automatic reflash.
"""
import datetime as dt
import json
from pathlib import Path
import re

import yaml

from bseed_pm_provision import Bridge, ROOT

OPTION = 'bseed_pm_telemetry_quarantine'


def guard_path(profile):
    path = Path(profile['workdir']).resolve() / 'PM_TELEMETRY_GUARD.json'
    if path.is_relative_to(ROOT):
        raise ValueError('telemetry guard evidence must be private')
    return path


def bridge_for(profile):
    config = yaml.safe_load(Path(profile['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    return Bridge(config, profile['broker'], profile['device'])


def target(bridge, profile):
    matches = [d for d in (bridge.inventory or []) if d.get('ieee_address') == profile['ieee']]
    if bridge.state != 'online' or len(matches) != 1:
        raise ValueError('telemetry guard requires online bridge and one exact target')
    device = matches[0]
    if (device.get('friendly_name'), device.get('manufacturer'), device.get('model_id')) != (
            profile['device'], 'b28wrpvx', 'TS011F-BS-PM'):
        raise ValueError('telemetry guard requires the exact custom PM socket')
    options = (device.get('definition') or {}).get('options') or []
    if not any(o.get('property') == OPTION for o in options):
        raise ValueError('install the candidate converter with telemetry quarantine before OTA')
    return device


def set_option(bridge, profile, enabled):
    response = bridge.request('options', {'id': profile['ieee'], 'options': {OPTION: enabled}},
                              full_response=True)
    data = response.get('data') or {}
    if (data.get('id') != profile['ieee'] or (data.get('to') or {}).get(OPTION) is not enabled
            or data.get('restart_required') is not False):
        raise ValueError('telemetry option change was not acknowledged as effective')
    return data


def begin(profile, campaign_token):
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    if path.exists():
        raise ValueError('existing telemetry guard requires identity recovery before another OTA')
    bridge = bridge_for(profile)
    try:
        bridge.start()
        target(bridge, profile)
        record = dict(ieee=profile['ieee'], device=profile['device'], sha256=profile['sha256'], token=campaign_token,
                      expected_build=profile['postflash_build'], expected_role=profile['postflash_role'],
                      started=dt.datetime.now(dt.timezone.utc).isoformat(), phase='enabling')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf8') as handle:
            json.dump(record, handle, indent=2)
        data = set_option(bridge, profile, True)
        if (data.get('from') or {}).get(OPTION) is True:
            raise ValueError('telemetry was already quarantined; do not take over another guard')
        record['phase'] = 'enabled'
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
    finally:
        bridge.stop()


def validate_release(profile, record, evidence, lock):
    if (record.get('ieee'), record.get('sha256'), record.get('expected_build'),
        record.get('expected_role'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], profile['postflash_build'], profile['postflash_role'], 'enabled'):
        raise ValueError('telemetry guard does not match this enabled campaign')
    if (lock.get('ieee'), lock.get('sha256'), lock.get('token'), lock.get('phase')) != (
            profile['ieee'], profile['sha256'], record.get('token'), 'ota_transfer_ok_postflash_unverified'):
        raise ValueError('telemetry release requires this campaign transfer to have succeeded')
    observed = evidence.get('at') or evidence.get('observed_at')
    if not observed or dt.datetime.fromisoformat(observed) < dt.datetime.fromisoformat(record['started']):
        raise ValueError('identity evidence predates telemetry quarantine')
    if evidence.get('result') == 'build_refreshed_postflash_unverified':
        if evidence.get('target') != profile['ieee'] or evidence.get('fresh_inventory_observed') is not True:
            raise ValueError('fresh target interview evidence missing')
        identity = evidence.get('after') or {}
    elif evidence.get('result') in ('metadata_refreshed', 'metadata_already_correct'):
        if evidence.get('error') is not None:
            raise ValueError('metadata recovery reported an error; PM telemetry stays quarantined')
        if evidence.get('ieee') != profile['ieee']:
            raise ValueError('metadata recovery targeted another device')
        if evidence.get('interview_ok') is not True:
            raise ValueError('metadata recovery lacks a transaction-matched successful interview')
        if evidence.get('fresh_inventory_observed') is not True:
            raise ValueError('metadata recovery lacks fresh nonretained inventory')
        if (evidence.get('live_zdo_after') or {}).get('role') != profile['postflash_role']:
            raise ValueError('final live role evidence missing after metadata recovery')
        identity = evidence.get('after') or {}
    else:
        raise ValueError('identity is still unconfirmed; PM telemetry stays quarantined')
    if (identity.get('ieee_address'), identity.get('software_build_id'), identity.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('post-update identity does not match quarantined campaign')


def validate_timeout_reconcile_release(profile, record, evidence, lock):
    """Allow only fresh installed-identity proof to release PM quarantine after
    an OTA response timeout. This never upgrades the OTA/network lock phase and
    never claims transport success."""
    if (record.get('ieee'), record.get('sha256'), record.get('expected_build'),
        record.get('expected_role'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], profile['postflash_build'], profile['postflash_role'], 'enabled'):
        raise ValueError('telemetry guard does not match this enabled campaign')
    if (lock.get('ieee'), lock.get('sha256'), lock.get('token'), lock.get('phase')) != (
            profile['ieee'], profile['sha256'], record.get('token'), 'update_timeout_or_unconfirmed'):
        raise ValueError('timeout reconciliation requires this exact unresolved campaign')
    observed = evidence.get('at') or evidence.get('observed_at')
    if not observed or dt.datetime.fromisoformat(observed) < dt.datetime.fromisoformat(record['started']):
        raise ValueError('identity evidence predates telemetry quarantine')
    if evidence.get('result') not in ('metadata_refreshed', 'metadata_already_correct'):
        raise ValueError('timeout reconciliation requires fresh metadata evidence')
    if evidence.get('error') is not None or evidence.get('ieee') != profile['ieee']:
        raise ValueError('metadata reconciliation targeted another device or reported an error')
    if evidence.get('interview_ok') is not True or evidence.get('fresh_inventory_observed') is not True:
        raise ValueError('timeout reconciliation requires one successful fresh target interview')
    live = evidence.get('live_zdo_after') or {}
    if live.get('role') != profile['postflash_role']:
        raise ValueError('timeout reconciliation lacks final live role evidence')
    identity = evidence.get('after') or {}
    if (identity.get('ieee_address'), identity.get('software_build_id'), identity.get('type')) != (
            profile['ieee'], profile['postflash_build'], profile['postflash_role']):
        raise ValueError('installed identity does not match quarantined campaign')
    return True


def validate_source_unchanged_release(profile, record, evidence, lock):
    """Release quarantine only when the attempted OTA stopped and fresh
    evidence proves the exact preflash build/role is still installed."""
    if (record.get('ieee'), record.get('sha256'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], 'enabled'):
        raise ValueError('telemetry guard does not match this enabled campaign')
    if (lock.get('ieee'), lock.get('sha256'), lock.get('token'), lock.get('phase')) != (
            profile['ieee'], profile['sha256'], record.get('token'),
            'source_unchanged_reconciled'):
        raise ValueError('source-unchanged release requires this exact reconciled campaign')
    observed = evidence.get('at') or evidence.get('observed_at')
    if not observed or dt.datetime.fromisoformat(observed) < dt.datetime.fromisoformat(record['started']):
        raise ValueError('source-unchanged evidence predates telemetry quarantine')
    if evidence.get('result') != 'source_unchanged_reconciled':
        raise ValueError('wrong source-unchanged evidence result')
    if (evidence.get('ieee'), evidence.get('source_build'), evidence.get('source_role')) != (
            profile['ieee'], profile['preflash_build'], profile['preflash_role']):
        raise ValueError('fresh source identity does not match preflash build/role')
    if evidence.get('update_available') is not True or evidence.get('ota_transport_success') is not False:
        raise ValueError('source-unchanged reconciliation requires pending exact update and no transport success')
    return True


def release_source_pending(profile, evidence_path):
    """Restore proven preflash-firmware PM telemetry without authorizing a new OTA.

    Network ownership must remain with this failed campaign; fresh load samples
    are required independently before candidate check and new flash.
    """
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    from bseed_ota_campaign import network_lock_path
    from bseed_network_campaign_lock import read_lock
    network = read_lock(network_lock_path(profile, required=True))
    if (record.get('ieee'), record.get('sha256'), record.get('phase')) != (
            profile['ieee'], profile['sha256'], 'enabled'):
        raise ValueError('PM source-pending quarantine identity mismatch')
    if (lock.get('phase'), lock.get('token'), lock.get('ieee'),
            lock.get('sha256'), lock.get('reconciliation_evidence')) != (
            'source_verified_candidate_pending', record.get('token'),
            profile['ieee'], profile['sha256'], str(evidence_path)):
        raise ValueError('PM source-pending work authority mismatch')
    if not network or (network.get('phase'), network.get('token'), network.get('ieee'),
                       network.get('image_sha256')) != (
            'source_verified_candidate_pending', record.get('token'),
            profile['ieee'], profile['sha256']):
        raise ValueError('PM source-pending network ownership mismatch')
    if (evidence.get('result'), evidence.get('ieee'), evidence.get('source_build'),
            evidence.get('source_role'), evidence.get('candidate_sha256'),
            evidence.get('fresh_get_build_role_verified')) != (
            'source_verified_candidate_pending', profile['ieee'],
            profile['preflash_build'], profile['preflash_role'],
            profile['sha256'], True):
        raise ValueError('PM release lacks genuine fresh preflash source identity')
    if dt.datetime.fromisoformat(evidence['at']) <= dt.datetime.fromisoformat(record['started']):
        raise ValueError('PM source evidence predates quarantine')
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (
                profile['preflash_build'], profile['preflash_role']):
            raise ValueError('PM source changed before telemetry restoration')
        set_option(bridge, profile, False)
        record.update(phase='released_source_pending',
                      evidence=str(evidence_path),
                      released_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                      transport_phase_preserved='source_verified_candidate_pending')
        path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
        path.rename(path.with_name(
            'PM_TELEMETRY_RELEASED_SOURCE_PENDING_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


def release_source_unchanged(profile, evidence_path):
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    validate_source_unchanged_release(profile, record, evidence, lock)
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (
                profile['preflash_build'], profile['preflash_role']):
            raise ValueError('current source identity changed since reconciliation evidence')
        set_option(bridge, profile, False)
        record.update(
            phase='released_source_unchanged',
            evidence=str(evidence_path),
            transport_phase_preserved='source_unchanged_reconciled',
        )
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
        path.rename(path.with_name(
            'PM_TELEMETRY_RELEASED_SOURCE_UNCHANGED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


def release_reconciled(profile, evidence_path):
    """Release only PM telemetry quarantine after timeout identity reconciliation.

    The campaign/network lock deliberately stays unresolved. Hardware acceptance
    and permission for another OTA are separate later decisions.
    """
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    validate_timeout_reconcile_release(profile, record, evidence, lock)
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (
                profile['postflash_build'], profile['postflash_role']):
            raise ValueError('current identity changed since timeout reconciliation evidence')
        set_option(bridge, profile, False)
        record.update(
            phase='released_after_timeout_identity_reconcile',
            evidence=str(evidence_path),
            transport_phase_preserved=lock.get('phase'),
        )
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
        path.rename(path.with_name(
            'PM_TELEMETRY_RELEASED_RECONCILED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


def release(profile, evidence_path):
    if profile.get('require_pm') is not True:
        return
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    validate_release(profile, record, evidence, lock)
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (profile['postflash_build'], profile['postflash_role']):
            raise ValueError('current identity changed since post-update evidence')
        set_option(bridge, profile, False)
        record.update(phase='released', evidence=str(evidence_path))
        # Preserve the record; never leave a released guard looking active.
        path.write_text(json.dumps(record, indent=2), encoding='utf8')
        path.rename(path.with_name('PM_TELEMETRY_RELEASED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


# Campaign phases that are unresolved because the update never reached its goal.
# ota_transfer_complete_recovery_deferred is the owner-cancelled campaign: its
# transport lock and network lease are already closed, and it deliberately
# retains the quarantine ("not a PM-quarantine release"). Accepted-style
# terminal phases have their own release routes and are never superseded.
SUPERSEDABLE_LOCK_PHASES = ('ota_transfer_ok_postflash_unverified', 'update_timeout_or_unconfirmed',
                            'ota_transfer_complete_recovery_deferred')
WIRED_SESSION_PHASES = ('BOOT_REQUESTED', 'ACTIVATED_VERIFIED', 'CLOSED')
MIN_REASON_CHARS = 30
MAX_EVIDENCE_AGE_SECONDS = 3600
MAX_CLOCK_SKEW_SECONDS = 300


def _utc(value, what):
    try:
        moment = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(what + ' has no valid timestamp') from None
    if moment.tzinfo is None:
        raise ValueError(what + ' timestamp lacks a timezone')
    return moment


def validate_wired_install_receipt(session, ieee, image_sha256):
    """The SWire session proves the stated image was the LAST thing written to THIS device by wire.

    It proves a write was sent, not that the image runs; the fresh identity
    evidence proves that. Returns the last write time and the image hash.
    """
    context = session.get('context') or {}
    image = context.get('imageSha256')
    if not (isinstance(image, str) and len(image) == 64 and all(c in '0123456789abcdef' for c in image)):
        raise ValueError('wired-install session lacks an image SHA-256')
    if image != image_sha256:
        raise ValueError('wired-install session wrote a different image than the stated superseding image')
    if not re.search('(?<![0-9a-f])' + re.escape(ieee.lower()) + '(?![0-9a-f])', str(context.get('targetId', '')).lower()):
        raise ValueError('wired-install session was not recorded for this device')
    if session.get('phase') not in WIRED_SESSION_PHASES:
        raise ValueError('wired-install session is not at a finished phase')
    # A flash is stage + activate; the activate receipt is the finished write.
    writes = [(_utc(receipt.get('utc'), 'wired-install receipt'), receipt.get('imageSha256'))
              for op in (session.get('operations') or {}).values()
              if isinstance(op, dict) and op.get('status') == 'COMPLETED'
              for receipt in (op.get('result') or {}).get('receipts', [])
              if receipt.get('action') == 'activate']
    if not any(sha == image for _, sha in writes):
        raise ValueError('wired-install session has no completed write of its image')
    written_at, last_image = max(writes, key=lambda item: item[0])
    if last_image != image:
        raise ValueError('a later wired write replaced the stated image')
    return {'image_sha256': image, 'written_at': written_at}


def validate_superseded_release(record, evidence, lock, session, *, ieee, build, role, reason, image_sha256,
                                now=None, max_age=MAX_EVIDENCE_AGE_SECONDS):
    """Release the PM quarantine of a campaign that a verified wired install replaced.

    The campaign's own image never ran, so the normal routes, which expect the
    campaign build, cannot apply. All of this is required: an enabled guard for
    this device, an unresolved campaign lock, a different installed build proven
    by a fresh transaction-matched interview with a live role, a SWire install
    receipt for this device written before that proof, and a written reason.
    """
    if (record.get('ieee'), record.get('phase')) != (ieee, 'enabled'):
        raise ValueError('telemetry guard is not an enabled guard for this device')
    if build == record.get('expected_build'):
        raise ValueError('the installed build is the campaign build: use the normal release route')
    if (lock.get('ieee'), lock.get('token')) != (ieee, record.get('token')):
        raise ValueError('campaign lock does not belong to this guard')
    if lock.get('phase') not in SUPERSEDABLE_LOCK_PHASES:
        raise ValueError('campaign lock is not an unresolved update: %s' % lock.get('phase'))
    if len((reason or '').strip()) < MIN_REASON_CHARS:
        raise ValueError('a written reason of at least %d characters is required' % MIN_REASON_CHARS)
    install = validate_wired_install_receipt(session, ieee, image_sha256)
    observed = _utc(evidence.get('at') or evidence.get('observed_at'), 'identity evidence')
    if observed < _utc(record.get('started'), 'telemetry guard'):
        raise ValueError('identity evidence predates telemetry quarantine')
    if observed <= install['written_at']:
        raise ValueError('identity evidence predates the wired install')
    now = now or dt.datetime.now(dt.timezone.utc)
    if observed > now + dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError('identity evidence is dated in the future')
    if now - observed > dt.timedelta(seconds=max_age):
        raise ValueError('identity evidence is older than %d seconds' % max_age)
    if evidence.get('result') not in ('metadata_refreshed', 'metadata_already_correct'):
        raise ValueError('identity is still unconfirmed; PM telemetry stays quarantined')
    if evidence.get('error') is not None or evidence.get('ieee') != ieee:
        raise ValueError('identity evidence targeted another device or reported an error')
    if evidence.get('interview_ok') is not True or evidence.get('fresh_inventory_observed') is not True:
        raise ValueError('identity evidence lacks one successful fresh target interview')
    if (evidence.get('live_zdo_after') or {}).get('role') != role:
        raise ValueError('identity evidence lacks the final live role')
    identity = evidence.get('after') or {}
    if (identity.get('ieee_address'), identity.get('software_build_id'), identity.get('type')) != (ieee, build, role):
        raise ValueError('fresh identity does not match the superseding build and role')
    return install


def release_superseded(profile, evidence_path, session_path, *, build, role, reason, image_sha256):
    """Release PM quarantine after a verified wired install replaced the campaign.

    Disables only this target's quarantine. The campaign and network locks are
    left exactly as they were: retiring them is a separate owner decision.
    Hardware acceptance is not implied.
    """
    path = guard_path(profile)
    record = json.loads(path.read_text(encoding='utf8'))
    evidence = json.loads(Path(evidence_path).read_text(encoding='utf8'))
    lock = json.loads((path.parent / 'ACTIVE_LOCK.json').read_text(encoding='utf8'))
    session = json.loads(Path(session_path).read_text(encoding='utf8'))
    def validate():
        return validate_superseded_release(record, evidence, lock, session, ieee=profile['ieee'],
                                           build=build, role=role, reason=reason, image_sha256=image_sha256)
    install = validate()
    released = path.with_name('PM_TELEMETRY_RELEASED_SUPERSEDED_' + str(lock['token']) + '.json')
    if released.exists():
        raise ValueError('a superseded release record already exists for this campaign')
    bridge = bridge_for(profile)
    try:
        bridge.start()
        device = target(bridge, profile)
        if (device.get('software_build_id'), device.get('type')) != (build, role):
            raise ValueError('current identity changed since the identity evidence')
        validate()  # the bridge may have been slow: the freshness window must still hold
        set_option(bridge, profile, False)
        record.update(
            phase='released_superseded_by_wired_install',
            released_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            superseding_build=build, superseding_role=role, superseding_image_sha256=install['image_sha256'],
            evidence=str(evidence_path), wired_install_session=str(session_path), reason=reason.strip(),
            transport_phase_preserved=lock.get('phase'))
        path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
        path.rename(path.with_name('PM_TELEMETRY_RELEASED_SUPERSEDED_' + str(lock['token']) + '.json'))
    finally:
        bridge.stop()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    sup = commands.add_parser('release-superseded', help=release_superseded.__doc__.splitlines()[0])
    for key in ('workdir', 'device', 'ieee', 'confirm-ieee', 'mqtt-config', 'broker', 'evidence',
                'wired-install-session', 'superseding-build', 'superseding-image-sha256', 'reason'):
        sup.add_argument('--' + key, required=True)
    sup.add_argument('--role', choices=['Router', 'EndDevice'], required=True)
    args = parser.parse_args(argv)
    if args.confirm_ieee != args.ieee or not re.fullmatch('0x[0-9a-f]{16}', args.ieee):
        raise SystemExit('exact IEEE confirmation required (0x and 16 lowercase hex digits, typed twice)')
    profile = {'workdir': args.workdir, 'device': args.device, 'ieee': args.ieee,
               'mqtt_config': args.mqtt_config, 'broker': args.broker, 'require_pm': True}
    release_superseded(profile, args.evidence, args.wired_install_session,
                       build=args.superseding_build, role=args.role, reason=args.reason,
                       image_sha256=args.superseding_image_sha256)
    print('PM telemetry quarantine released for %s; campaign locks unchanged.' % args.device)


if __name__ == '__main__':
    main()
