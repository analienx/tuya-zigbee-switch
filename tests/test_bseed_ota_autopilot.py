"""Same-role OTA autopilot. GitHub-hosted CI only; no network, MQTT or device."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_ota_autopilot as ap  # noqa: E402

IEEE = '0xa4c1380011223344'


def profile(tmp_path, **extra):
    p = {'device': 'SocketX', 'ieee': IEEE, 'sha256': 'a' * 64, 'workdir': str(tmp_path / 'work'),
         'preflash_role': 'EndDevice', 'postflash_role': 'EndDevice', 'request_timeout_ms': 300000}
    p.update(extra)
    return p


def test_progress_gate_tolerates_a_few_duds_but_stops_a_stall():
    gate = ap.progress_gate
    assert gate([], 2)['allow_retry']
    assert gate([{'max_progress': 12}, {'max_progress': 15}], 2) == {
        'best_progress': 15, 'stagnant_attempts': 0, 'allow_retry': True}
    # 39 -> 0 (sent nothing) -> 39.5: the dud is forgiven, the new best resets it.
    assert gate([{'max_progress': 39}, {'max_progress': 0}, {'max_progress': 39.5}], 2)['stagnant_attempts'] == 0
    assert gate([{'max_progress': 39}, {'max_progress': 0}, {'max_progress': 38}], 2)['allow_retry']
    assert not gate([{'max_progress': 39}, {'max_progress': 0}, {'max_progress': 38}, {'max_progress': 39}], 2)['allow_retry']


def test_policy_refuses_role_changes_and_live_rejoin_beside_a_peer(tmp_path):
    with pytest.raises(ValueError, match='same-role only'):
        ap.validate_policy(profile(tmp_path, postflash_role='Router'))
    with pytest.raises(ValueError, match='source_rejoin_strategy=none'):
        ap.validate_policy(profile(tmp_path, parallel_ota_max=3, source_rejoin_strategy='auto'))
    with pytest.raises(ValueError, match='permit-join-all'):
        ap.validate_policy(profile(tmp_path, parallel_ota_max=2, source_rejoin_strategy='none',
                                   allow_join_all_fallback=True))
    assert ap.validate_policy(profile(tmp_path)) == []
    assert '15 min' in ap.validate_policy(profile(tmp_path, request_timeout_ms=900000))[0]
    assert ap.validate_policy(profile(tmp_path, request_timeout_ms=1800000)) == []  # FORCE minimum
    assert ap.join_strategy(profile(tmp_path, parallel_ota_max=2, source_rejoin_strategy='none')) == 'none'
    assert ap.join_strategy(profile(tmp_path)) == 'auto'


def test_attempt_summary_reads_progress_duration_and_error(tmp_path):
    log = tmp_path / 'ota_bseed-ota-x.jsonl'
    rows = [{'when': '2026-11-21T18:27:58+01:00', 'event': 'ota_request_sent', 'value': {}},
            {'when': '2026-11-21T18:28:30+01:00', 'event': 'device_state', 'value': {'update': {'progress': 37.4}}},
            {'when': '2026-11-21T18:30:35+01:00', 'event': 'device_state', 'value': {'update': {'progress': 39.0}}},
            {'when': '2026-11-21T18:45:48+01:00', 'event': 'ota_final',
             'value': {'phase': 'update_error', 'response': {'error': 'did not start/finish firmware download'}}}]
    log.write_text('\n'.join(json.dumps(r) for r in rows) + '\nnot json\n')
    s = ap.attempt_summary(log)
    assert (s['first_progress'], s['last_progress'], s['phase']) == (37.4, 39.0, 'update_error')
    assert s['started'].startswith('2026-11-21T18:27:58') and 'download' in s['error']
    assert ap.attempt_summary(None) == {'log': None}


def test_retry_step_retries_only_failures():
    calls, sleeps = iter([1, 0]), []
    assert ap.retry_step(lambda: next(calls), 2, 5, sleeps.append) == (0, 2)
    assert sleeps == [5]
    assert ap.retry_step(lambda: 1, 1, 0, lambda _s: None) == (1, 2)


class Fake:
    """Drives the autopilot through scripted campaign outcomes."""

    def __init__(self, tmp_path, monkeypatch, cfg, flash_results, postflash=('postflash_candidate',)):
        self.work = Path(cfg['workdir'])
        self.work.mkdir(parents=True)
        self.flash_results, self.postflash = list(flash_results), list(postflash)
        self.history, self.calls, self.joins = [], [], []
        self.preflight_failures = 0
        sup = ap.sup
        monkeypatch.setattr(sup.campaign, 'load_profile', lambda _p: dict(cfg))
        monkeypatch.setattr(sup, 'ota_progress_history', lambda _w, _p=None: list(self.history))
        monkeypatch.setattr(sup, 'latest_ota_jsonl', lambda _w: None)
        monkeypatch.setattr(sup, 'restore_pm_source_telemetry', lambda _p, _w: None)
        monkeypatch.setattr(sup, 'release_pm_after_deferred_reconcile', lambda _p, _w: None)
        monkeypatch.setattr(sup, 'run_logged', self.run_logged)
        monkeypatch.setattr(sup, 'reconcile_until_ready', self.reconcile)
        monkeypatch.setattr(ap.time, 'sleep', lambda _s: None)
        self.served = True
        monkeypatch.setattr(ap, 'served_image_ok', lambda _p: self.served)

    def set_phase(self, phase):
        (self.work / 'ACTIVE_LOCK.json').write_text(json.dumps({'phase': phase}))

    def run_logged(self, cmd, _log):
        mode = cmd[cmd.index('--mode') + 1]
        self.calls.append(mode)
        if mode == 'preflight' and self.preflight_failures:
            self.preflight_failures -= 1
            return 1
        if mode == 'flash':
            phase, progress = self.flash_results.pop(0)
            self.set_phase(phase)
            if progress is not None:
                self.history.append({'max_progress': progress})
            return 0 if phase in ('postflash_candidate', ap.POSTFLASH_PENDING) else 2
        if mode == 'postflash':
            self.set_phase(self.postflash.pop(0))
        return 0

    def reconcile(self, _path, _ieee, _work, _log, **kw):
        self.joins.append(kw['join_strategy'])
        assert kw['fast'] is True and kw['allow_join_all_fallback'] is False
        self.set_phase(ap.sup.READY_PHASE)
        return ap.sup.READY_PHASE


def test_resumes_until_installed_without_live_rejoin_beside_a_peer(tmp_path, monkeypatch):
    cfg = profile(tmp_path, parallel_ota_max=3, source_rejoin_strategy='none')
    fake = Fake(tmp_path, monkeypatch, cfg, [('update_error', 12.4), ('update_error', 27.0),
                                            ('preflight_abort', None), (ap.POSTFLASH_PENDING, 100.0)],
                postflash=('postflash_unconfirmed_x', 'postflash_candidate'))
    pilot = ap.Autopilot(Path(cfg['workdir']) / 'profile.json', IEEE)
    result = pilot.run()
    assert result['state'] == 'installed'
    assert fake.calls.count('flash') == 4 and fake.calls.count('postflash') == 2
    assert fake.joins == ['none', 'none']
    assert [a['ended_phase'] for a in result['attempts']] == [
        'update_error', 'update_error', 'preflight_abort', ap.POSTFLASH_PENDING]
    saved = json.loads((fake.work / ap.STATE_FILE).read_text())
    assert saved['state'] == 'installed' and len(saved['attempts']) == 4


def test_stops_when_progress_no_longer_improves(tmp_path, monkeypatch):
    cfg = profile(tmp_path)
    fake = Fake(tmp_path, monkeypatch, cfg, [('update_error', 30.0), ('update_error', 20.0),
                                            ('update_error', 25.0), ('update_error', 29.0)])
    result = ap.Autopilot(Path(cfg['workdir']) / 'p.json', IEEE, max_stagnant=2).run()
    assert result['state'] == 'progress_stalled'
    assert fake.calls.count('flash') == 4


def test_never_touches_a_transfer_another_process_runs(tmp_path, monkeypatch):
    cfg = profile(tmp_path)
    fake = Fake(tmp_path, monkeypatch, cfg, [])
    fake.set_phase('ota_running')
    assert ap.Autopilot(Path(cfg['workdir']) / 'p.json', IEEE).run()['state'] == 'blocked_active_transport'
    assert fake.calls == []


def test_a_dropped_preflight_is_retried_before_flashing(tmp_path, monkeypatch):
    cfg = profile(tmp_path)
    fake = Fake(tmp_path, monkeypatch, cfg, [('postflash_candidate', 100.0)])
    fake.preflight_failures = 1
    assert ap.Autopilot(Path(cfg['workdir']) / 'p.json', IEEE).run()['state'] == 'installed'
    assert fake.calls[:3] == ['preflight', 'preflight', 'check']


def test_progress_logged_only_by_zigbee2mqtt_counts(tmp_path):
    log = tmp_path / 'ota_bseed-ota-1.jsonl'
    log.write_text('\n'.join(json.dumps(r) for r in [
        {'event': 'z2m_log', 'value': {'message': "zh:controller:ota: OTA update of '%s' at 39.21%%, 52 seconds" % IEEE}},
        {'event': 'z2m_log', 'value': {'message': "zh:controller:ota: OTA update of '%s' at 39.53%%, 9 seconds" % IEEE}},
        {'event': 'z2m_log', 'value': {'message': "zh:controller:ota: OTA update of '0xa4c138da1333dc70' at 46.2%, x"}}]))
    assert ap.log_progress(log, IEEE.upper().replace('0X', '0x')) == 39.53
    assert ap.log_progress(tmp_path / 'missing.jsonl', IEEE) == 0.0


def test_a_missing_served_image_is_republished_or_reported(tmp_path, monkeypatch):
    cfg = profile(tmp_path)
    fake = Fake(tmp_path, monkeypatch, cfg, [('postflash_candidate', 100.0)])
    fake.served = False
    result = ap.Autopilot(Path(cfg['workdir']) / 'p.json', IEEE).run()
    assert result['state'] == 'image_not_served' and 'flash' not in fake.calls
    published = []

    def republish(_profile, site):
        published.append(site)
        fake.served = True
    monkeypatch.setattr(ap, 'republish', republish)
    site = {'publish': {'remote_dir': '/config/www/x', 'ssh_alias': 'ha'}}
    assert ap.Autopilot(Path(cfg['workdir']) / 'p.json', IEEE, site=site).run()['state'] == 'installed'
    assert published == [site]


def test_republish_refuses_a_local_image_that_differs_and_uploads_the_right_folder(tmp_path):
    image = tmp_path / 'SocketX-a-to-b.ota'
    image.write_bytes(b'image')
    index = tmp_path / 'index.json'
    index.write_text('[]')
    import hashlib
    p = {'image': str(image), 'index_output': str(index), 'sha256': hashlib.sha256(b'image').hexdigest(),
         'url': 'http://ha.invalid/local/bseed-canary/SocketX-a-to-b/firmware.ota?v=1'}
    site = {'publish': {'remote_dir': '/config/www/bseed-canary', 'ssh_alias': 'ha'}}
    calls = []
    ap.republish(p, site, call=lambda cmd, stdin=None: calls.append(cmd) or 0)
    assert calls[0][-1] == 'mkdir -p /config/www/bseed-canary/SocketX-a-to-b'
    assert calls[1][-1] == 'ha:/config/www/bseed-canary/SocketX-a-to-b/firmware.ota'
    assert calls[2][-1] == 'ha:/config/www/bseed-canary/SocketX-a-to-b/index.json'
    with pytest.raises(RuntimeError, match='differs'):
        ap.republish(dict(p, sha256='0' * 64), site, call=lambda *a, **k: 0)


def group(tmp_path, n, slots=5, drop=None):
    ieees = ['0xa4c13800000000%02d' % i for i in range(n)]
    out = []
    for i, ieee in enumerate(ieees):
        peers = [x for x in ieees if x != ieee and x != drop]
        out.append(profile(tmp_path, device='S%d' % i, ieee=ieee, workdir=str(tmp_path / ('w%d' % i)),
                           parallel_ota_max=slots, concurrent_ota_ieees=peers, source_rejoin_strategy='none'))
    return out


def test_parallel_group_must_allow_every_member(tmp_path):
    ap.validate_group(group(tmp_path, 5))
    ap.validate_group(group(tmp_path, 1, slots=1))
    with pytest.raises(ValueError, match='1..5'):
        ap.validate_group(group(tmp_path, 6))
    with pytest.raises(ValueError, match='below the group size'):
        ap.validate_group(group(tmp_path, 4, slots=3))
    members = group(tmp_path, 3, drop='0xa4c1380000000002')
    with pytest.raises(ValueError, match='lacks 0xa4c1380000000002'):
        ap.validate_group(members)
    twins = group(tmp_path, 2)
    twins[1]['ieee'] = twins[0]['ieee']
    with pytest.raises(ValueError, match='distinct'):
        ap.validate_group(twins)


def test_run_many_launches_one_autopilot_per_socket_and_reports_each(tmp_path, monkeypatch):
    members = group(tmp_path, 3)
    by_path = {Path(m['workdir']) / 'profile.json': m for m in members}
    monkeypatch.setattr(ap.sup.campaign, 'load_profile', lambda p: dict(by_path[Path(p)]))
    launched, sleeps = [], []
    for i, m in enumerate(members):
        Path(m['workdir']).mkdir()
        (Path(m['workdir']) / ap.STATE_FILE).write_text(json.dumps(
            {'state': 'installed' if i else 'progress_stalled', 'phase': 'x',
             'attempts': [{}] * (i + 1), 'progress_gate': {'best_progress': 40.0 + i}}))
    alive = iter([True, False, False, False])
    results = ap.run_many(list(by_path), [m['ieee'] for m in members], extra=['--max-attempts', '9'],
                          launch=lambda cmd, log: launched.append(cmd) or len(launched),
                          alive=lambda pid: next(alive, False), sleep=sleeps.append)
    assert [c[c.index('--confirm-ieee') + 1] for c in launched] == [m['ieee'] for m in members]
    assert all(c[3] == 'run' and c[-2:] == ['--max-attempts', '9'] for c in launched)
    assert sleeps[:2] == [20, 20]  # staggered admissions
    assert [r['state'] for r in results] == ['progress_stalled', 'installed', 'installed']
    assert results[2]['attempts'] == 3 and results[0]['best_progress'] == 40.0
    with pytest.raises(ValueError, match='same order'):
        ap.run_many(list(by_path), [m['ieee'] for m in reversed(members)], extra=[])


def test_exact_ieee_and_unloaded_confirmation_are_required(tmp_path, monkeypatch):
    cfg = profile(tmp_path, non_pm=True)
    Fake(tmp_path, monkeypatch, cfg, [])
    with pytest.raises(ValueError, match='IEEE'):
        ap.Autopilot(Path('p.json'), '0x0000000000000000')
    with pytest.raises(ValueError, match='load-unplugged'):
        ap.Autopilot(Path('p.json'), IEEE)
