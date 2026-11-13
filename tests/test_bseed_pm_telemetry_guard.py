"""Telemetry remains quarantined until the exact OTA has fresh identity proof."""
import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_pm_telemetry_guard import (validate_release, validate_timeout_reconcile_release,
                                      validate_source_unchanged_release, set_option, OPTION)
import bseed_pm_telemetry_guard as guard


def fixture():
    p = dict(ieee='0x0011223344556677', sha256='a'*64, postflash_build='1.2.5-bseedcli11', postflash_role='EndDevice')
    record = dict(ieee=p['ieee'], sha256=p['sha256'], expected_build=p['postflash_build'],
                  expected_role=p['postflash_role'], started='2026-09-27T10:00:00+00:00', phase='enabled', token='campaign-1')
    lock = dict(ieee=p['ieee'], sha256=p['sha256'], phase='ota_transfer_ok_postflash_unverified', token='campaign-1')
    evidence = dict(at='2026-09-27T11:00:00+00:00', target=p['ieee'], fresh_inventory_observed=True,
        result='build_refreshed_postflash_unverified', after=dict(ieee_address=p['ieee'],
            software_build_id=p['postflash_build'], type=p['postflash_role']))
    return p, record, evidence, lock


def test_only_matching_fresh_identity_releases_quarantine():
    validate_release(*fixture())


@pytest.mark.parametrize('failure', ['old', 'cached', 'failed', 'wrong_role', 'wrong_build', 'wrong_device', 'other_ota', 'unapplied', 'unacknowledged'])
def test_unconfirmed_or_foreign_evidence_cannot_release(failure):
    p, r, e, lock = fixture()
    if failure == 'old': e['at'] = '2026-09-26T10:00:00+00:00'
    if failure == 'cached': e['fresh_inventory_observed'] = False
    if failure == 'failed': e['result'] = 'build_mismatch_after_interview'
    if failure == 'wrong_role': e['after']['type'] = 'Router'
    if failure == 'wrong_build': e['after']['software_build_id'] = '1.2.5-bseedv8u4'
    if failure == 'wrong_device': e['after']['ieee_address'] = '0x0000000000000000'
    if failure == 'other_ota': lock['token'] = 'campaign-2'
    if failure == 'unapplied': lock['phase'] = 'ota_running'
    if failure == 'unacknowledged': r['phase'] = 'enabling'
    with pytest.raises(ValueError): validate_release(p, r, e, lock)


def metadata_fixture():
    p, r, _, lock = fixture()
    evidence = dict(at='2026-09-27T11:00:00+00:00', ieee=p['ieee'], device='TestSocket',
        result='metadata_refreshed', error=None, interview_ok=True, fresh_inventory_observed=True,
        live_zdo_before={'role': 'EndDevice'}, live_zdo_after={'role': 'EndDevice'},
        inventory_role_before='Router', inventory_role_after='EndDevice',
        after=dict(ieee_address=p['ieee'], software_build_id=p['postflash_build'], type=p['postflash_role']),
        interview_response={'status': 'ok'}, automatic_remove_or_factory_reset=False)
    return p, r, evidence, lock


@pytest.mark.parametrize('result', ['metadata_refreshed', 'metadata_already_correct'])
def test_metadata_recovery_releases_with_final_fresh_evidence(result):
    p, r, e, lock = metadata_fixture()
    e['result'] = result
    validate_release(p, r, e, lock)


@pytest.mark.parametrize('failure', ['rejoin_shape', 'legacy_shape', 'error', 'no_interview',
    'stale_inventory', 'missing_live_role', 'wrong_live_role', 'stale_role',
    'wrong_build', 'wrong_device', 'wrong_ieee', 'old'])
def test_metadata_recovery_keeps_guard_active_without_final_proof(failure):
    p, r, e, lock = metadata_fixture()
    if failure == 'rejoin_shape':
        e = dict(observed_at=e['at'], ieee=p['ieee'], result='postflash_candidate',
                 inventory=e['after'], live_node_descriptor={'role': 'EndDevice'})
    if failure == 'legacy_shape':
        for key in ('interview_ok', 'fresh_inventory_observed', 'live_zdo_after', 'after', 'error'):
            e.pop(key, None)
    if failure == 'error': e['error'] = 'RuntimeError: Targeted interview failed or timed out'
    if failure == 'no_interview': e['interview_ok'] = False
    if failure == 'stale_inventory': e['fresh_inventory_observed'] = False
    if failure == 'missing_live_role': e['live_zdo_after'] = None
    if failure == 'wrong_live_role': e['live_zdo_after'] = {'role': 'Router'}
    if failure == 'stale_role': e['after']['type'] = 'Router'
    if failure == 'wrong_build': e['after']['software_build_id'] = '1.2.5-bseedv8u4'
    if failure == 'wrong_device': e['after']['ieee_address'] = '0x0000000000000000'
    if failure == 'wrong_ieee': e['ieee'] = '0x0000000000000000'
    if failure == 'old': e['at'] = '2026-09-26T10:00:00+00:00'
    with pytest.raises(ValueError): validate_release(p, r, e, lock)


def test_timeout_reconcile_allows_only_fresh_exact_metadata_identity():
    p, r, e, lock = metadata_fixture()
    lock['phase'] = 'update_timeout_or_unconfirmed'
    validate_timeout_reconcile_release(p, r, e, lock)
    with pytest.raises(ValueError, match='transfer to have succeeded'):
        validate_release(p, r, e, lock)


@pytest.mark.parametrize('failure', [
    'wrong_phase', 'wrong_token', 'no_interview', 'stale_inventory',
    'wrong_live_role', 'wrong_build', 'wrong_ieee', 'old',
])
def test_timeout_reconcile_keeps_quarantine_without_exact_installed_proof(failure):
    p, r, e, lock = metadata_fixture()
    lock['phase'] = 'update_timeout_or_unconfirmed'
    if failure == 'wrong_phase': lock['phase'] = 'update_error'
    if failure == 'wrong_token': lock['token'] = 'campaign-2'
    if failure == 'no_interview': e['interview_ok'] = False
    if failure == 'stale_inventory': e['fresh_inventory_observed'] = False
    if failure == 'wrong_live_role': e['live_zdo_after'] = {'role': 'Router'}
    if failure == 'wrong_build': e['after']['software_build_id'] = 'another-build'
    if failure == 'wrong_ieee': e['ieee'] = '0x0000000000000000'
    if failure == 'old': e['at'] = '2026-09-26T10:00:00+00:00'
    with pytest.raises(ValueError):
        validate_timeout_reconcile_release(p, r, e, lock)


def test_source_unchanged_release_requires_exact_preflash_identity_and_pending_update():
    p, r, _, lock = fixture()
    p.update(preflash_build='1.2.5-bseedcli12', preflash_role='EndDevice')
    lock['phase'] = 'source_unchanged_reconciled'
    evidence = dict(
        at='2026-09-27T11:00:00+00:00',
        result='source_unchanged_reconciled',
        ieee=p['ieee'],
        source_build=p['preflash_build'],
        source_role=p['preflash_role'],
        update_available=True,
        ota_transport_success=False,
    )
    validate_source_unchanged_release(p, r, evidence, lock)
    for key, value in (
        ('source_build', 'wrong-build'),
        ('source_role', 'Router'),
        ('update_available', False),
        ('ota_transport_success', True),
    ):
        broken = copy.deepcopy(evidence)
        broken[key] = value
        with pytest.raises(ValueError):
            validate_source_unchanged_release(p, r, broken, lock)


def test_effective_option_ack_is_required():
    p, _, _, _ = fixture()
    class Bridge:
        data = dict(id=p['ieee'], to={OPTION: True}, restart_required=False)
        def request(self, operation, payload, *, full_response):
            assert operation == 'options' and payload == {'id': p['ieee'], 'options': {OPTION: True}}
            assert full_response is True
            return {'status': 'ok', 'data': self.data}
    bridge = Bridge()
    set_option(bridge, p, True)
    bridge.data['restart_required'] = True
    with pytest.raises(ValueError): set_option(bridge, p, True)


def test_quarantine_precedes_ota_submission_and_release_follows_identity():
    runner = (ROOT / 'helper_scripts/bseed_targeted_z2m_ota.py').read_text()
    campaign = (ROOT / 'helper_scripts/bseed_ota_campaign.py').read_text()
    assert runner.index('begin(source_profile, token)') < runner.index("campaign['phase'] = 'ota_running'")
    assert campaign.index('release(profile, interview)') > campaign.index('if interviewed:')
    assert campaign.index('release(profile, metadata_evidence)') > campaign.index('if refreshed:')
    assert 'release(profile, join_evidence)' not in campaign
    assert campaign.index('TELEMETRY_GUARD_STAYS_ACTIVE') > campaign.index("if args.mode == 'metadata':")


def lifecycle(tmp_path, monkeypatch):
    """No MQTT: exercise actual private-record lifecycle with a fake bridge."""
    p, _, evidence, lock = fixture()
    p.update(require_pm=True, device='TestSocket', workdir=str(tmp_path / 'campaign'))

    class Bridge:
        state = 'online'
        started = 0
        stopped = 0
        fail_ack = False
        already_enabled = False

        def __init__(self):
            self.requests = []
            self.inventory = [dict(ieee_address=p['ieee'], friendly_name=p['device'],
                manufacturer='b28wrpvx', model_id='TS011F-BS-PM', type=p['postflash_role'],
                software_build_id=p['postflash_build'], definition={'options': [{'property': OPTION}]})]

        def start(self): self.started += 1
        def stop(self): self.stopped += 1

        def request(self, operation, payload, *, full_response):
            assert operation == 'options' and full_response is True
            assert payload['id'] == p['ieee']
            self.requests.append(payload)
            if self.fail_ack:
                raise TimeoutError('simulated lost acknowledgement')
            return dict(status='ok', data=dict(id=p['ieee'],
                to=payload['options'], **{'from': {OPTION: self.already_enabled}}, restart_required=False))

    bridge = Bridge()
    monkeypatch.setattr(guard, 'bridge_for', lambda profile: bridge)
    return p, evidence, lock, bridge


def release_evidence(profile, evidence, lock):
    directory = Path(profile['workdir'])
    (directory / 'ACTIVE_LOCK.json').write_text(json.dumps(lock))
    evidence['at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    path = directory / 'interview.json'
    path.write_text(json.dumps(evidence))
    return path


def test_timeout_reconcile_release_archives_guard_but_preserves_campaign_lock(tmp_path, monkeypatch):
    p, _, lock, bridge = lifecycle(tmp_path, monkeypatch)
    guard.begin(p, lock['token'])
    active = guard.guard_path(p)
    _, _, evidence, _ = metadata_fixture()
    lock['phase'] = 'update_timeout_or_unconfirmed'
    path = release_evidence(p, evidence, lock)
    guard.release_reconciled(p, path)
    assert not active.exists()
    archive = active.with_name('PM_TELEMETRY_RELEASED_RECONCILED_' + lock['token'] + '.json')
    record = json.loads(archive.read_text())
    assert record['phase'] == 'released_after_timeout_identity_reconcile'
    assert record['transport_phase_preserved'] == 'update_timeout_or_unconfirmed'
    assert json.loads((Path(p['workdir']) / 'ACTIVE_LOCK.json').read_text())['phase'] == 'update_timeout_or_unconfirmed'
    assert [r['options'][OPTION] for r in bridge.requests] == [True, False]


def test_guard_lifecycle_preserves_and_archives_exact_campaign(tmp_path, monkeypatch):
    p, evidence, lock, bridge = lifecycle(tmp_path, monkeypatch)
    guard.begin(p, lock['token'])
    active = guard.guard_path(p)
    record = json.loads(active.read_text())
    assert record['phase'] == 'enabled' and record['token'] == lock['token']
    path = release_evidence(p, evidence, lock)
    guard.release(p, path)
    assert not active.exists()
    archive = active.with_name('PM_TELEMETRY_RELEASED_' + lock['token'] + '.json')
    assert json.loads(archive.read_text())['phase'] == 'released'
    assert [r['options'][OPTION] for r in bridge.requests] == [True, False]
    assert bridge.started == bridge.stopped == 2


@pytest.mark.parametrize('failure', ['offline', 'wrong_ieee', 'converter_missing'])
def test_begin_refuses_before_option_write_when_target_is_unverified(tmp_path, monkeypatch, failure):
    p, _, lock, bridge = lifecycle(tmp_path, monkeypatch)
    if failure == 'offline': bridge.state = 'offline'
    if failure == 'wrong_ieee': bridge.inventory[0]['ieee_address'] = '0x0000000000000000'
    if failure == 'converter_missing': bridge.inventory[0]['definition']['options'] = []
    with pytest.raises(ValueError): guard.begin(p, lock['token'])
    assert not bridge.requests and not guard.guard_path(p).exists()
    assert bridge.started == bridge.stopped == 1


@pytest.mark.parametrize('failure', ['lost_ack', 'existing_quarantine'])
def test_uncertain_begin_retains_evidence_and_blocks_new_campaign(tmp_path, monkeypatch, failure):
    p, _, lock, bridge = lifecycle(tmp_path, monkeypatch)
    bridge.fail_ack = failure == 'lost_ack'
    bridge.already_enabled = failure == 'existing_quarantine'
    with pytest.raises((ValueError, TimeoutError)): guard.begin(p, lock['token'])
    active = guard.guard_path(p)
    before = active.read_bytes()
    assert json.loads(before)['phase'] == 'enabling'
    with pytest.raises(ValueError, match='existing telemetry guard'):
        guard.begin(p, 'another-campaign')
    assert active.read_bytes() == before and len(bridge.requests) == 1


@pytest.mark.parametrize('failure', ['identity_changed', 'release_ack_lost'])
def test_uncertain_release_keeps_active_record(tmp_path, monkeypatch, failure):
    p, evidence, lock, bridge = lifecycle(tmp_path, monkeypatch)
    guard.begin(p, lock['token'])
    path = release_evidence(p, evidence, lock)
    active = guard.guard_path(p)
    before = active.read_bytes()
    if failure == 'identity_changed': bridge.inventory[0]['software_build_id'] = 'another-build'
    if failure == 'release_ack_lost': bridge.fail_ack = True
    with pytest.raises((ValueError, TimeoutError)): guard.release(p, path)
    assert active.read_bytes() == before
    assert not list(active.parent.glob('PM_TELEMETRY_RELEASED_*'))
    assert len(bridge.requests) == (1 if failure == 'identity_changed' else 2)
    assert bridge.started == bridge.stopped == 2


# --- release after a verified wired install superseded the campaign -------------

WIRED_IMAGE = 'b' * 64
NOW = dt.datetime(2026, 11, 13, 19, 0, tzinfo=dt.timezone.utc)


def superseded_fixture():
    """Table Left shape: the r21 campaign never ran; r22 was written by wire."""
    p, record, _, lock = fixture()
    p.update(postflash_build='r21', postflash_role='Router')
    record.update(expected_build='r21', expected_role='Router', started='2026-11-06T08:13:31+00:00')
    # Shape of a real `telink_swire_recovery.py` session after a TX-only flash.
    session = dict(
        phase='CLOSED',
        context=dict(imageSha256=WIRED_IMAGE, targetId='tableleft-' + p['ieee'], slot='A'),
        operations={'flash-1': dict(
            request=dict(action='flash', imageSha256=WIRED_IMAGE, slot='A', targetId='tableleft-' + p['ieee']),
            status='COMPLETED',
            result=dict(status='SENT_UNVERIFIED', action='flash', phase='BOOT_REQUESTED', receipts=[
                dict(utc='2026-11-13T13:26:12+00:00', action='stage', imageSha256=WIRED_IMAGE, result='SENT_UNVERIFIED'),
                dict(utc='2026-11-13T13:26:13+00:00', action='activate', imageSha256=WIRED_IMAGE,
                     result='SENT_UNVERIFIED')]))})
    evidence = dict(at='2026-11-13T18:50:00+00:00', ieee=p['ieee'], result='metadata_refreshed', error=None,
                    interview_ok=True, fresh_inventory_observed=True, live_zdo_after={'role': 'Router'},
                    after=dict(ieee_address=p['ieee'], software_build_id='r22', type='Router'))
    kwargs = dict(ieee=p['ieee'], build='r22', role='Router', now=NOW, image_sha256=WIRED_IMAGE,
                  reason='r21 never started; r22 was written by SWire and answers as r22')
    return record, evidence, lock, session, kwargs


def test_wired_install_supersedes_the_campaign_with_fresh_identity():
    record, evidence, lock, session, kwargs = superseded_fixture()
    install = guard.validate_superseded_release(record, evidence, lock, session, **kwargs)
    assert install['image_sha256'] == WIRED_IMAGE
    assert install['written_at'] == dt.datetime(2026, 11, 13, 13, 26, 13, tzinfo=dt.timezone.utc)
    for phase in ('update_timeout_or_unconfirmed', 'ota_transfer_complete_recovery_deferred'):
        lock['phase'] = phase
        guard.validate_superseded_release(record, evidence, lock, session, **kwargs)


@pytest.mark.parametrize('failure, message', [
    ('campaign_build', 'normal release route'),
    ('terminal_lock', 'not an unresolved update'),
    ('abandoned_lock', 'not an unresolved update'),
    ('other_lock', 'does not belong'),
    ('guard_not_enabled', 'enabled guard'),
    ('other_device_guard', 'enabled guard'),
    ('short_reason', 'written reason'),
    ('old_evidence', 'older than'),
    ('predates_guard', 'predates telemetry quarantine'),
    ('predates_install', 'predates the wired install'),
    ('cached_inventory', 'fresh target interview'),
    ('no_interview', 'fresh target interview'),
    ('failed_metadata', 'still unconfirmed'),
    ('error', 'reported an error'),
    ('wrong_live_role', 'final live role'),
    ('wrong_build', 'does not match the superseding'),
    ('wrong_ieee_after', 'does not match the superseding'),
    ('evidence_other_device', 'another device'),
    ('naive_time', 'timezone'),
    ('future_evidence', 'dated in the future'),
    ('other_image_stated', 'different image'),
])
def test_superseded_release_needs_every_proof(failure, message):
    record, evidence, lock, session, kwargs = superseded_fixture()
    if failure == 'campaign_build': record['expected_build'] = 'r22'
    if failure == 'terminal_lock': lock['phase'] = 'postflash_accepted'
    if failure == 'abandoned_lock': lock['phase'] = 'source_unchanged_abandoned'
    if failure == 'other_lock': lock['token'] = 'campaign-2'
    if failure == 'guard_not_enabled': record['phase'] = 'enabling'
    if failure == 'other_device_guard': record['ieee'] = '0x0000000000000000'
    if failure == 'short_reason': kwargs['reason'] = 'r22 is installed'
    if failure == 'old_evidence': evidence['at'] = '2026-11-13T15:00:00+00:00'
    if failure == 'predates_guard': evidence['at'] = '2026-11-01T00:00:00+00:00'
    if failure == 'predates_install': evidence['at'] = '2026-11-13T13:00:00+00:00'; kwargs['now'] = dt.datetime(2026, 11, 13, 13, 5, tzinfo=dt.timezone.utc)
    if failure == 'cached_inventory': evidence['fresh_inventory_observed'] = False
    if failure == 'no_interview': evidence['interview_ok'] = False
    if failure == 'failed_metadata': evidence['result'] = 'build_mismatch_after_interview'
    if failure == 'error': evidence['error'] = 'RuntimeError: interview timed out'
    if failure == 'wrong_live_role': evidence['live_zdo_after'] = {'role': 'EndDevice'}
    if failure == 'wrong_build': evidence['after']['software_build_id'] = 'r21'
    if failure == 'wrong_ieee_after': evidence['after']['ieee_address'] = '0x0000000000000000'
    if failure == 'evidence_other_device': evidence['ieee'] = '0x0000000000000000'
    if failure == 'naive_time': evidence['at'] = '2026-11-13T18:50:00'
    if failure == 'future_evidence': evidence['at'] = '2026-11-14T18:50:00+00:00'
    if failure == 'other_image_stated': kwargs['image_sha256'] = 'c' * 64
    with pytest.raises(ValueError, match=message):
        guard.validate_superseded_release(record, evidence, lock, session, **kwargs)


@pytest.mark.parametrize('failure, message', [
    ('no_hash', 'SHA-256'),
    ('other_device', 'not recorded for this device'),
    ('running_session', 'finished phase'),
    ('not_completed', 'no completed write'),
    ('other_image_completed', 'no completed write'),
    ('stage_only', 'no completed write'),
    ('receipt_other_image', 'no completed write'),
    ('no_receipts', 'no completed write'),
    ('bad_time', 'valid timestamp'),
    ('longer_hex_id', 'not recorded for this device'),
    ('later_other_write', 'later wired write replaced'),
])
def test_wired_install_receipt_must_belong_to_this_device_and_finished_write(failure, message):
    _, _, _, session, kwargs = superseded_fixture()
    op = session['operations']['flash-1']
    if failure == 'no_hash': session['context']['imageSha256'] = 'abc'
    if failure == 'other_device': session['context']['targetId'] = 'kitchen-0x00124b0000000001'
    if failure == 'running_session': session['phase'] = 'STAGED'
    if failure == 'not_completed': op['status'] = 'STARTED'
    if failure == 'other_image_completed':
        for receipt in op['result']['receipts']: receipt['imageSha256'] = 'c' * 64
    if failure == 'stage_only': op['result']['receipts'] = op['result']['receipts'][:1]
    if failure == 'receipt_other_image': op['result']['receipts'][1]['imageSha256'] = 'c' * 64
    if failure == 'no_receipts': op['result']['receipts'] = []
    if failure == 'bad_time': op['result']['receipts'][1]['utc'] = 'yesterday'  # the activate receipt
    if failure == 'longer_hex_id': session['context']['targetId'] = 'tableleft-' + kwargs['ieee'] + 'ab'
    if failure == 'later_other_write':
        session['operations']['flash-2'] = dict(request=dict(action='flash', imageSha256='c' * 64), status='COMPLETED',
            result=dict(receipts=[dict(utc='2026-11-13T14:00:00+00:00', action='activate', imageSha256='c' * 64)]))
    with pytest.raises(ValueError, match=message):
        guard.validate_wired_install_receipt(session, kwargs['ieee'], WIRED_IMAGE)


def test_superseded_release_disables_only_the_quarantine_and_keeps_locks(tmp_path, monkeypatch):
    record, evidence, lock, session, kwargs = superseded_fixture()
    workdir = tmp_path / 'campaign'
    workdir.mkdir()
    profile = dict(ieee=kwargs['ieee'], device='TestSocket', workdir=str(workdir), broker='b', mqtt_config='m',
                   require_pm=True)
    (workdir / 'PM_TELEMETRY_GUARD.json').write_text(json.dumps(record))
    (workdir / 'ACTIVE_LOCK.json').write_text(json.dumps(lock))
    session_path, evidence_path = tmp_path / 'session.json', tmp_path / 'evidence.json'
    session_path.write_text(json.dumps(session))
    evidence['at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    evidence_path.write_text(json.dumps(evidence))

    class Bridge:
        state, requests, started, stopped = 'online', [], 0, 0
        build = 'r22'
        inventory = [dict(ieee_address=kwargs['ieee'], friendly_name='TestSocket', manufacturer='b28wrpvx',
                          model_id='TS011F-BS-PM', type='Router', software_build_id='r22',
                          definition={'options': [{'property': OPTION}]})]
        def start(self): self.started += 1
        def stop(self): self.stopped += 1
        def request(self, operation, payload, *, full_response):
            self.requests.append(payload)
            return dict(status='ok', data=dict(id=kwargs['ieee'], to=payload['options'],
                                               **{'from': {OPTION: True}}, restart_required=False))
    bridge = Bridge()
    monkeypatch.setattr(guard, 'bridge_for', lambda _profile: bridge)
    guard.release_superseded(profile, evidence_path, session_path, build='r22', role='Router',
                             reason=kwargs['reason'], image_sha256=WIRED_IMAGE)
    assert bridge.requests == [{'id': kwargs['ieee'], 'options': {OPTION: False}}]
    assert bridge.started == bridge.stopped == 1
    assert not (workdir / 'PM_TELEMETRY_GUARD.json').exists()
    released = json.loads((workdir / 'PM_TELEMETRY_RELEASED_SUPERSEDED_campaign-1.json').read_text())
    assert released['phase'] == 'released_superseded_by_wired_install'
    assert released['superseding_build'] == 'r22' and released['superseding_image_sha256'] == WIRED_IMAGE
    assert released['transport_phase_preserved'] == 'ota_transfer_ok_postflash_unverified'
    assert json.loads((workdir / 'ACTIVE_LOCK.json').read_text()) == lock, 'campaign lock is untouched'

    # A second release must stop before touching the live option.
    (workdir / 'PM_TELEMETRY_GUARD.json').write_text(json.dumps(record))
    bridge.requests.clear()
    with pytest.raises(ValueError, match='already exists'):
        guard.release_superseded(profile, evidence_path, session_path, build='r22', role='Router',
                                 reason=kwargs['reason'], image_sha256=WIRED_IMAGE)
    assert bridge.requests == []


def test_superseded_release_stops_before_any_change_when_identity_moved(tmp_path, monkeypatch):
    record, evidence, lock, session, kwargs = superseded_fixture()
    workdir = tmp_path / 'campaign'
    workdir.mkdir()
    profile = dict(ieee=kwargs['ieee'], device='TestSocket', workdir=str(workdir), broker='b', mqtt_config='m')
    for name, data in (('PM_TELEMETRY_GUARD.json', record), ('ACTIVE_LOCK.json', lock)):
        (workdir / name).write_text(json.dumps(data))
    session_path, evidence_path = tmp_path / 'session.json', tmp_path / 'evidence.json'
    session_path.write_text(json.dumps(session))
    evidence['at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    evidence_path.write_text(json.dumps(evidence))

    class Bridge:
        state, requests = 'online', []
        inventory = [dict(ieee_address=kwargs['ieee'], friendly_name='TestSocket', manufacturer='b28wrpvx',
                          model_id='TS011F-BS-PM', type='Router', software_build_id='r23-other',
                          definition={'options': [{'property': OPTION}]})]
        def start(self): pass
        def stop(self): pass
        def request(self, *_args, **_kwargs): self.requests.append(1)
    bridge = Bridge()
    monkeypatch.setattr(guard, 'bridge_for', lambda _profile: bridge)
    with pytest.raises(ValueError, match='identity changed'):
        guard.release_superseded(profile, evidence_path, session_path, build='r22', role='Router',
                                 reason=kwargs['reason'], image_sha256=WIRED_IMAGE)
    assert bridge.requests == [] and (workdir / 'PM_TELEMETRY_GUARD.json').exists()


def test_cli_requires_exact_ieee_confirmation():
    base = ['release-superseded', '--workdir', 'w', '--device', 'd', '--ieee', '0xa4c138cdf963eac3',
            '--mqtt-config', 'm', '--broker', 'b', '--evidence', 'e', '--wired-install-session', 's',
            '--superseding-build', 'r22', '--superseding-image-sha256', 'b' * 64,
            '--role', 'Router', '--reason', 'x' * 40]
    with pytest.raises(SystemExit, match='exact IEEE confirmation'):
        guard.main(base + ['--confirm-ieee', '0x0000000000000000'])
    upper = [a.replace('0xa4c138cdf963eac3', '0xA4C138CDF963EAC3') for a in base]
    with pytest.raises(SystemExit, match='lowercase hex'):
        guard.main(upper + ['--confirm-ieee', '0xA4C138CDF963EAC3'])
    with pytest.raises(SystemExit):
        guard.main(base)
