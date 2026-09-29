"""Offline Router PM OTA gate; no network/relay/flash operations.

Subprocess boundaries are mocked, but the telemetry quarantine lifecycle is
real: the fake runner performs the actual guard begin/lock side effects and
the real release() validates them. Only the MQTT bridge (external I/O) is
faked.
"""
import datetime as dt
import json,sys
import uuid
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
import bseed_ota_campaign as campaign
import bseed_pm_telemetry_guard as guard
from bseed_pm_telemetry_guard import OPTION
from test_bseed_ota_campaign import _router_candidate_profile


def fake_bridge_for(profile):
    class Bridge:
        state = 'online'
        def __init__(self):
            self.requests = []
            self.inventory = [dict(ieee_address=profile['ieee'], friendly_name=profile['device'],
                manufacturer='b28wrpvx', model_id='TS011F-BS-PM', type=profile['postflash_role'],
                software_build_id=profile['postflash_build'], definition={'options': [{'property': OPTION}]})]
        def start(self): pass
        def stop(self): pass
        def request(self, operation, payload, *, full_response):
            assert operation == 'options' and full_response is True
            self.requests.append(payload)
            return dict(status='ok', data=dict(id=profile['ieee'], to=payload['options'],
                **{'from': {OPTION: False}}, restart_required=False))
    return Bridge()


def runner_side_effect(profile, *, skip_begin):
    """Model the real runner/reinterview subprocess contracts: exit code plus
    the evidence files the campaign consumes next."""
    def fake_call(argv, **kwargs):
        name = Path(argv[2]).name
        if name == 'bseed_targeted_z2m_ota.py' and not skip_begin:
            token = 'bseed-ota-' + uuid.uuid4().hex
            from bseed_network_campaign_lock import acquire
            acquire(campaign.network_lock_path(profile, required=True),
                    network_id=profile['network_id'], token=token,
                    device=profile['device'], ieee=profile['ieee'], image_sha256=profile['sha256'])
            guard.begin(profile, token)
            work = Path(profile['workdir'])
            work.mkdir(parents=True, exist_ok=True)
            (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
                ieee=profile['ieee'], sha256=profile['sha256'], token=token,
                phase='ota_transfer_ok_postflash_unverified')))
            return 0
        if name == 'bseed_z2m_postota_reinterview.py':
            output = Path(argv[argv.index('--output') + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(dict(
                at=dt.datetime.now(dt.timezone.utc).isoformat(), target=profile['ieee'],
                fresh_inventory_observed=True, result='build_refreshed_postflash_unverified',
                after=dict(ieee_address=profile['ieee'], software_build_id=profile['postflash_build'],
                           type=profile['postflash_role']))))
            return 0
        return 0
    return fake_call


def run_flash(cfg, path, monkeypatch, *, skip_begin=False):
    cfg.update(network_lock_dir=str(Path(cfg['workdir']).parent / 'authority'),
               network_id='test-network', network_lock_shared=True)
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'flash',
                                      '--confirm-ieee', cfg['ieee']])
    bridge = fake_bridge_for(cfg)
    monkeypatch.setattr(guard, 'bridge_for', lambda profile: bridge)
    with patch('bseed_ota_campaign.subprocess.call',
               side_effect=runner_side_effect(cfg, skip_begin=skip_begin)) as call:
        with pytest.raises(SystemExit) as done:
            campaign.main()
    return done, call, bridge


def test_validated_router_flash_never_runs_client_provision(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    path=tmp_path/'router_profile.json';path.write_text(json.dumps(cfg))
    done, call, bridge = run_flash(cfg, path, monkeypatch)
    assert done.value.code==0
    commands=[x.args[0] for x in call.call_args_list]
    assert [Path(x[2]).name for x in commands]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py',
        'bseed_z2m_postflash_verify.py','bseed_pm_role_audit.py']
    assert '--require-pm' in commands[2] and '--apply' not in str(commands)
    assert '--relay-get-key' in commands[0] and 'state_relay' in commands[0]
    assert 'bseed_pm_provision.py' not in str(commands)
    archive = list(Path(cfg['workdir']).glob('PM_TELEMETRY_RELEASED_*.json'))
    assert len(archive) == 1 and json.loads(archive[0].read_text())['phase'] == 'released'
    assert [r['options'][OPTION] for r in bridge.requests] == [True, False]


def test_router_release_without_valid_guard_fails_before_audit(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    path=tmp_path/'router_profile.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    bridge = fake_bridge_for(cfg)
    monkeypatch.setattr(guard, 'bridge_for', lambda profile: bridge)
    with patch('bseed_ota_campaign.subprocess.call',
               side_effect=runner_side_effect(cfg, skip_begin=True)) as call:
        with pytest.raises(FileNotFoundError):
            campaign.main()
    assert [Path(x.args[0][2]).name for x in call.call_args_list]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py']
    assert not list(Path(cfg['workdir']).glob('PM_TELEMETRY_RELEASED_*.json'))


def test_router_flash_never_retries_or_provisions_after_failed_transport(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    path=tmp_path/'router_profile.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=2) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==2 and len(call.call_args_list)==1
