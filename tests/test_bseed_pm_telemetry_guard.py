"""Telemetry remains quarantined until the exact OTA has fresh identity proof."""
import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_pm_telemetry_guard import validate_release, set_option, OPTION
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
