"""Offline tests of profile orchestration; no live MQTT, HTTP or firmware changes."""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_campaign as campaign
import bseed_pm_telemetry_guard as telemetry_guard
from bseed_pm_telemetry_guard import release as real_telemetry_release


@pytest.fixture(autouse=True)
def mock_telemetry_release_for_subprocess_composition(monkeypatch):
    # These tests mock successful interview subprocesses and do not generate
    # their private evidence. Real release validation has its own negative suite.
    monkeypatch.setattr('bseed_pm_telemetry_guard.release', lambda *args: None)


def profile(tmp_path):
    image=tmp_path/'image.ota'; image.write_bytes(b'unsigned offline fixture')
    template=tmp_path/'template.json'
    template.write_text(json.dumps([{'manufacturerCode':4417,'imageType':54179,
       'manufacturerName':['stock-manufacturer'], 'fileVersion':4294967295}]),encoding='utf8')
    return {'device':'TestSocket','ieee':'0x0011223344556677','manufacturer':'stock-manufacturer',
      'model':'TS011F','preflash_role':'Router','image':str(image),
      'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
      'url':'http://example.invalid/image.ota', 'mqtt_config':str(tmp_path/'private-mqtt.yaml'),
      'broker':'127.0.0.1','workdir':str(tmp_path/'campaign'),'manufacturer_code':4417,
      'image_type':54179,'file_version':'0xffffffff','expect_relay':'ON',
      'index_url':'http://example.invalid/private-index.json',
      'index_output':str(tmp_path/'private-index.json'),'template_index':str(template),
      'postflash_role':'EndDevice','postflash_build':'expected-client', 'block_bytes':32}


def test_profile_rejects_missing_private_fields(tmp_path):
    source=tmp_path/'target.json';source.write_text('{}')
    with pytest.raises(ValueError,match='Missing profile fields'): campaign.load_profile(source)


def test_image_index_requires_exact_tuple_and_hash(tmp_path):
    cfg=profile(tmp_path);cfg['sha256']='0'*64
    with pytest.raises(ValueError,match='SHA256'): campaign.make_index(cfg)
    cfg=profile(tmp_path);cfg['image_type']=42
    with pytest.raises(ValueError,match='exactly one'): campaign.make_index(cfg)


def test_private_index_stages_exact_single_image(tmp_path, monkeypatch):
    # This test covers index composition with synthetic non-firmware bytes.
    # Registry/payload policy is exercised with complete OTA fixtures separately.
    monkeypatch.setattr('bseed_socket_version_policy.require_increasing', lambda *args: {})
    cfg=profile(tmp_path)
    with patch('bseed_targeted_z2m_ota.verify_image',return_value=(b'', [0,0,0,0,4417,54179])):
        result=campaign.make_index(cfg)
    entry=json.loads(Path(cfg['index_output']).read_text())[0]
    assert entry['url']==cfg['url'] and entry['sha512']==hashlib.sha512(Path(cfg['image']).read_bytes()).hexdigest()
    assert entry['fileSize']==len(Path(cfg['image']).read_bytes()) and result['image_sha256']==cfg['sha256']
    with patch('bseed_targeted_z2m_ota.verify_image',return_value=(b'', [0,0,0,0,4417,54179])):
        assert campaign.make_index(cfg)==result
    Path(cfg['index_output']).write_text('OTHER',encoding='utf8')
    with patch('bseed_targeted_z2m_ota.verify_image',return_value=(b'', [0,0,0,0,4417,54179])):
        with pytest.raises(ValueError,match='Refusing to overwrite'): campaign.make_index(cfg)


def test_runner_args_pass_optional_paced_response_delay(tmp_path):
    cfg=profile(tmp_path)
    cmd=campaign.runner_args(cfg,'flash')
    assert '--response-delay-ms' not in cmd
    cfg['response_delay_ms']=1200
    cmd=campaign.runner_args(cfg,'flash')
    assert cmd[cmd.index('--response-delay-ms')+1]=='1200'
    assert '--request-timeout-ms' not in cmd
    cfg['request_timeout_ms']=1800000
    cmd=campaign.runner_args(cfg,'flash')
    assert cmd[cmd.index('--request-timeout-ms')+1]=='1800000'


def test_runner_args_explicitly_preserve_device_and_block_limit(tmp_path):
    cfg=profile(tmp_path)
    cmd=campaign.runner_args(cfg,'flash')
    assert cmd[cmd.index('--ieee')+1]==cfg['ieee']
    assert cmd[cmd.index('--mode')+1]=='flash'
    assert cmd[cmd.index('--max-block-bytes')+1]=='32'
    assert cmd[cmd.index('--workdir')+1]==cfg['workdir']


def test_flash_requires_exact_second_ieee(tmp_path,monkeypatch):
    cfg=profile(tmp_path);path=tmp_path/'profile.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash'])
    with pytest.raises(SystemExit,match='Flash refused'):campaign.main()
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash','--confirm-ieee','0xBAD'])
    with pytest.raises(SystemExit,match='Flash refused'):campaign.main()


def test_cross_role_flash_is_refused_without_rejoin_orchestration(tmp_path, monkeypatch):
    cfg=profile(tmp_path);path=tmp_path/'role.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with pytest.raises(SystemExit,match='Cross-role flash refused'):campaign.main()


def test_transition_requires_scoped_rejoin_route_before_flashing(tmp_path,monkeypatch):
    cfg=profile(tmp_path);path=tmp_path/'role.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','transition',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError,match='join_via'):campaign.main()
        call.assert_not_called()


def test_transition_orders_flash_join_metadata_postflash(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg['join_via']='KnownRouter';path=tmp_path/'role.json'
    path.write_text(json.dumps(cfg));monkeypatch.setattr(sys,'argv',
        ['campaign','--profile',str(path),'--mode','transition','--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==0
    assert len(call.call_args_list)==4
    invoked=[args.args[0] for args in call.call_args_list]
    assert ['bseed_targeted_z2m_ota.py','bseed_z2m_rejoin_window.py',
            'bseed_z2m_metadata_refresh.py','bseed_z2m_postflash_verify.py']==[Path(a[2]).name for a in invoked]


def test_pm_profile_passes_strict_postflash_readiness_flag(tmp_path):
    cfg=profile(tmp_path)
    cfg['require_pm']=True
    assert '--require-pm' in campaign.postflash_cmd(cfg,tmp_path/'evidence.json')
    cfg['require_pm']=False
    assert '--require-pm' not in campaign.postflash_cmd(cfg,tmp_path/'evidence.json')


def test_pm_provision_command_requires_exact_target_and_private_ssh(tmp_path):
    cfg=profile(tmp_path);cfg['require_pm']=True
    with pytest.raises(ValueError,match='Confirm'):
        campaign.provision_cmd(cfg,'0xOTHER',tmp_path/'evidence.json')
    with pytest.raises(ValueError,match='pm_ssh_host'):
        campaign.provision_cmd(cfg,cfg['ieee'],tmp_path/'evidence.json')
    cfg.update(pm_ssh_host='127.0.0.1',pm_ssh_key=str(tmp_path/'key'),
               pm_max_writes=4,pm_allow_configure=True)
    cmd=campaign.provision_cmd(cfg,cfg['ieee'],tmp_path/'evidence.json')
    assert cmd[cmd.index('--confirm-ieee')+1]==cfg['ieee']
    assert cmd[cmd.index('--max-writes')+1]=='4'
    assert '--allow-configure' in cmd and '--apply' in cmd


def test_pm_transition_fails_before_firmware_write_if_provision_unavailable(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,join_via='KnownRouter')
    path=tmp_path/'role.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','transition',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError,match='pm_ssh_host'):campaign.main()
        call.assert_not_called()


def test_pm_transition_orders_provision_before_postflash(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,join_via='KnownRouter',
           pm_ssh_host='127.0.0.1',pm_ssh_key=str(tmp_path/'key'))
    path=tmp_path/'role.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','transition',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==0
    assert [Path(x.args[0][2]).name for x in call.call_args_list]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_rejoin_window.py',
        'bseed_z2m_metadata_refresh.py','bseed_pm_provision.py','bseed_z2m_postflash_verify.py']


def test_same_role_pm_flash_runs_provision_and_postflash(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,preflash_role='EndDevice',
        pm_ssh_host='127.0.0.1',pm_ssh_key=str(tmp_path/'key'))
    path=tmp_path/'pm.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as call:
        with pytest.raises(SystemExit) as done: campaign.main()
    assert done.value.code==0
    assert [Path(x.args[0][2]).name for x in call.call_args_list]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py',
        'bseed_pm_provision.py','bseed_z2m_postflash_verify.py']


def test_same_role_pm_flash_transport_failure_never_provisions(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,preflash_role='EndDevice',
        pm_ssh_host='127.0.0.1',pm_ssh_key=str(tmp_path/'key'))
    path=tmp_path/'pm.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=2) as call:
        with pytest.raises(SystemExit) as done: campaign.main()
    assert done.value.code==2 and len(call.call_args_list)==1


def test_router_pm_auto_provision_and_flash_fail_before_transfer(tmp_path, monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,postflash_role='Router',
         pm_ssh_host='test.invalid',pm_ssh_key=str(tmp_path/'key'))
    with pytest.raises(ValueError,match='Client-only'):
        campaign.provision_cmd(cfg,cfg['ieee'],tmp_path/'evidence.json')
    source=tmp_path/'router_profile.json';source.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(source),'--mode',
                                    'flash','--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError,match='build-matrix evidence'):
            campaign.main()
        call.assert_not_called()


def test_role_aware_pm_audit_is_read_only_and_accepts_router_profile(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(require_pm=True,postflash_role='Router',
        pm_ssh_host='test.invalid',pm_ssh_key=str(tmp_path/'key'))
    cmd=campaign.role_audit_cmd(cfg,tmp_path/'audit.json')
    assert 'bseed_pm_role_audit.py' in cmd[2] and '--apply' not in cmd
    source=tmp_path/'router_profile.json';source.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(source),'--mode','audit-pm'])
    with patch('bseed_ota_campaign.subprocess.call',return_value=2) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==2
    assert '--apply' not in call.call_args.args[0]


def _router_candidate_profile(tmp_path):
    cfg=profile(tmp_path);cfg.update(require_pm=True,postflash_role='Router',
        postflash_build='1.2.5-bseedv8u5-rc1',image_type=43556,
        relay_get_key='state_relay',pm_ssh_host='test.invalid')
    key=tmp_path/'private.key';key.write_text('fixture');cfg['pm_ssh_key']=str(key)
    baseline=tmp_path/'baseline.json';baseline.write_text('{}')
    cfg['pm_settings_baseline']=str(baseline)
    manifest=tmp_path/'ROLE_MATRIX.json'
    manifest.write_text(json.dumps({'compiledBothRoles':True,'hardwareAcceptance':False,
        'artifacts':[{'role':'Router','sha256':cfg['sha256'],'imageType':43556,
                      'build':cfg['postflash_build']},{'role':'EndDevice'}]}))
    cfg['pm_router_matrix_evidence']=str(manifest)
    return cfg

def test_router_candidate_requires_exact_matrix_and_relay_proof(tmp_path):
    cfg=_router_candidate_profile(tmp_path)
    assert campaign.verified_router_pm_candidate(cfg)
    cfg['relay_get_key']='state'
    with pytest.raises(ValueError,match='relay endpoint'):
        campaign.verified_router_pm_candidate(cfg)
    cfg['relay_get_key']='state_relay';cfg['sha256']='0'*64
    with pytest.raises(ValueError,match='differs'):
        campaign.verified_router_pm_candidate(cfg)


def test_same_role_router_interview_precedes_audit_and_postflash(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    src=tmp_path/'router.json';src.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(src),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as run:
        with pytest.raises(SystemExit) as result:campaign.main()
    assert result.value.code==0
    assert [Path(c.args[0][2]).name for c in run.call_args_list]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py',
        'bseed_z2m_postflash_verify.py','bseed_pm_role_audit.py']


def test_same_role_interview_failure_blocks_provision_and_postflash(tmp_path,monkeypatch):
    cfg=profile(tmp_path);cfg.update(preflash_role='EndDevice',require_pm=True,
        pm_ssh_host='test.invalid',pm_ssh_key=str(tmp_path/'private.key'))
    src=tmp_path/'pm.json';src.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(src),'--mode','flash',
                                    '--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',side_effect=[0,2]) as run:
        with pytest.raises(SystemExit) as result:campaign.main()
    assert result.value.code==2
    assert [Path(c.args[0][2]).name for c in run.call_args_list]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py']


def test_postota_interview_pins_image_and_separate_postflash_identity(tmp_path):
    cfg=profile(tmp_path)
    cfg.update(preflash_role='Router',postflash_role='Router',
               postflash_manufacturer='b28wrpvx',postflash_model='TS011F-BS-PM')
    cmd=campaign.reinterview_cmd(cfg,cfg['ieee'],tmp_path/'postota.json')
    assert cmd[cmd.index('--image-sha256')+1]==cfg['sha256']
    assert cmd[cmd.index('--manufacturer')+1]=='b28wrpvx'
    assert cmd[cmd.index('--model')+1]=='TS011F-BS-PM'
    assert cmd[cmd.index('--campaign-lock')+1]==str(Path(cfg['workdir'])/'ACTIVE_LOCK.json')
    assert '--confirm-ieee' in cmd and cmd[cmd.index('--confirm-ieee')+1]==cfg['ieee']
    assert '--apply' not in cmd and '--mode' not in cmd


def test_same_role_postflash_requires_exact_private_relay_energy_baseline(tmp_path):
    cfg=profile(tmp_path)
    cfg.update(preflash_role='Router',postflash_role='Router',require_pm=True,
               relay_get_key='state_relay')
    cmd=campaign.postflash_cmd(cfg,tmp_path/'post.json')
    assert cmd[cmd.index('--preflash-lock')+1]==str(Path(cfg['workdir'])/'ACTIVE_LOCK.json')
    assert cmd[cmd.index('--expected-image-sha256')+1]==cfg['sha256']
    assert cmd[cmd.index('--relay-get-key')+1]=='state_relay'
    assert '--require-pm' in cmd
    cfg['postflash_role']='EndDevice'
    assert '--preflash-lock' not in campaign.postflash_cmd(cfg,tmp_path/'cross_role.json')

def _pm_profile(tmp_path):
    cfg = profile(tmp_path)
    cfg.update(manufacturer='b28wrpvx', model='TS011F-BS-PM',
               preflash_role='EndDevice', postflash_role='EndDevice',
               postflash_build='1.2.5-bseedcli11')
    return cfg


def _nonpm_profile(tmp_path):
    cfg = profile(tmp_path)
    cfg.update(manufacturer='o1jzcxou', model='TS011F-BS', non_pm=True,
               require_pm=False, preflash_role='EndDevice',
               postflash_role='EndDevice', preflash_build='1.1.3-bseedc6',
               preflash_relay_physical_mode='follow_state',
               postflash_build='1.1.3-bseedc6')
    return cfg


def _write_profile(tmp_path, cfg, name='profile.json'):
    path = tmp_path / name
    path.write_text(json.dumps(cfg))
    return path


@pytest.mark.parametrize('bad', [False, 0, 1, 'true', 'false', 'True', '', [], {}, ['x']])
def test_custom_pm_profile_rejects_non_boolean_require_pm(tmp_path, bad):
    cfg = _pm_profile(tmp_path)
    cfg['require_pm'] = bad
    with pytest.raises(ValueError, match='require_pm=true|explicit Boolean'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


def test_custom_pm_profile_rejects_missing_require_pm(tmp_path):
    cfg = _pm_profile(tmp_path)
    assert 'require_pm' not in cfg
    with pytest.raises(ValueError, match='require_pm=true'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


def test_custom_pm_profile_with_boolean_true_loads(tmp_path):
    cfg = _pm_profile(tmp_path)
    cfg['require_pm'] = True
    assert campaign.load_profile(_write_profile(tmp_path, cfg))['require_pm'] is True


def test_custom_pm_profile_rejects_non_pm_mode_and_model_mismatch(tmp_path):
    cfg = _pm_profile(tmp_path)
    cfg.update(require_pm=True, non_pm=True, preflash_build='x',
               preflash_relay_physical_mode='y')
    with pytest.raises(ValueError, match='require_pm=true'):
        campaign.load_profile(_write_profile(tmp_path, cfg))
    cfg = _pm_profile(tmp_path)
    cfg.update(require_pm=True, model='TS011F')
    with pytest.raises(ValueError, match='require_pm=true'):
        campaign.load_profile(_write_profile(tmp_path, cfg, 'mismatch.json'))


def test_custom_pm_missing_require_pm_fails_before_prepare_and_check(tmp_path, monkeypatch):
    cfg = _pm_profile(tmp_path)
    path = _write_profile(tmp_path, cfg)
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'prepare'])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=AssertionError('no subprocess')) as call:
        with pytest.raises(ValueError, match='require_pm=true'):
            campaign.main()
    call.assert_not_called()
    assert not Path(cfg['index_output']).exists()
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', str(path), '--mode', 'check'])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=AssertionError('no subprocess')) as call:
        with pytest.raises(ValueError, match='require_pm=true'):
            campaign.main()
    call.assert_not_called()


def test_custom_nonpm_profile_with_boolean_false_loads(tmp_path):
    loaded = campaign.load_profile(_write_profile(tmp_path, _nonpm_profile(tmp_path)))
    assert loaded['require_pm'] is False and loaded['non_pm'] is True


@pytest.mark.parametrize('mutation', [
    {'require_pm': True}, {'require_pm': 'false'}, {'require_pm': 0},
    {'non_pm': False}, {'non_pm': 'yes'}, {'model': 'TS011F'},
    {'preflash_build': None},
])
def test_custom_nonpm_profile_rejects_contradictions(tmp_path, mutation):
    cfg = _nonpm_profile(tmp_path)
    if 'preflash_build' in mutation:
        cfg.pop('preflash_build')
    else:
        cfg.update(mutation)
    with pytest.raises(ValueError, match='require_pm=false|explicit Boolean'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


def test_custom_nonpm_profile_rejects_missing_non_pm_flag(tmp_path):
    cfg = _nonpm_profile(tmp_path)
    cfg.pop('non_pm')
    with pytest.raises(ValueError, match='require_pm=false'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


def _ts0726_profile(tmp_path):
    cfg = profile(tmp_path)
    cfg.update(manufacturer='iedhxgyi', model='TS0726-3-BS', require_pm=False,
               preflash_role='Router', postflash_role='Router',
               postflash_build='1.1.8-bseedv8')
    return cfg


def test_custom_ts0726_profile_with_boolean_false_loads(tmp_path):
    loaded = campaign.load_profile(_write_profile(tmp_path, _ts0726_profile(tmp_path)))
    assert loaded['require_pm'] is False


def test_custom_ts0726_profile_rejects_missing_require_pm(tmp_path):
    cfg = _ts0726_profile(tmp_path)
    cfg.pop('require_pm')
    with pytest.raises(ValueError, match='require_pm=false'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


@pytest.mark.parametrize('mutation', [{'require_pm': True}, {'require_pm': 'false'},
    {'model': 'TS011F'}, {'manufacturer': 'b28wrpvx'}])
def test_custom_ts0726_profile_rejects_contradictions(tmp_path, mutation):
    cfg = _ts0726_profile(tmp_path)
    cfg.update(mutation)
    with pytest.raises(ValueError, match='require_pm'):
        campaign.load_profile(_write_profile(tmp_path, cfg))


def test_stock_ts0726_profile_keeps_separate_contract(tmp_path):
    cfg = _ts0726_profile(tmp_path)
    cfg.update(manufacturer='_TZ3002_iedhxgyi', model='TS0726')
    cfg.pop('require_pm')
    loaded = campaign.load_profile(_write_profile(tmp_path, cfg, 'stock-ts0726.json'))
    assert 'require_pm' not in loaded


def test_runner_check_rejects_invalid_pm_campaign_profile_before_network(tmp_path, monkeypatch):
    cfg = _pm_profile(tmp_path)  # missing require_pm
    path = _write_profile(tmp_path, cfg)
    import bseed_targeted_z2m_ota as runner
    argv = ['runner', '--mode', 'check', '--device', cfg['device'], '--ieee', cfg['ieee'],
           '--manufacturer', cfg['manufacturer'], '--model', cfg['model'], '--role', 'EndDevice',
           '--image', cfg['image'], '--sha256', cfg['sha256'], '--url', cfg['url'],
           '--mqtt-config', cfg['mqtt_config'], '--broker', cfg['broker'],
           '--workdir', cfg['workdir'], '--manufacturer-code', '4417',
           '--image-type', '65024', '--file-version', '0x12053014',
           '--expect-relay', 'ON', '--index-url', cfg['index_url'],
           '--campaign-profile', str(path)]
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(ValueError, match='require_pm=true'):
        runner.main()


def _pm_transition_profile(tmp_path):
    cfg = _pm_profile(tmp_path)
    cfg.update(require_pm=True, preflash_role='Router', postflash_role='EndDevice',
               postflash_build='1.2.5-bseedcli11', join_via='KnownRouter',
               pm_ssh_host='127.0.0.1', pm_ssh_key=str(tmp_path / 'key'))
    return cfg


class _FakePmBridge:
    """No MQTT: exact-target identity with the candidate converter option."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = 'online'
        self.requests = []
        self.inventory = [dict(ieee_address=cfg['ieee'], friendly_name=cfg['device'],
            manufacturer='b28wrpvx', model_id='TS011F-BS-PM', type=cfg['postflash_role'],
            software_build_id=cfg['postflash_build'],
            definition={'options': [{'property': telemetry_guard.OPTION}]})]

    def start(self): pass
    def stop(self): pass

    def request(self, operation, payload, *, full_response):
        assert operation == 'options' and full_response is True
        self.requests.append(payload)
        return dict(status='ok', data=dict(id=self.cfg['ieee'], to=payload['options'],
            **{'from': {telemetry_guard.OPTION: False}}, restart_required=False))


def _begin_pm_campaign_guard(tmp_path, monkeypatch, cfg, token):
    loaded = campaign.load_profile(_write_profile(tmp_path, cfg, 'pm-transition.json'))
    work = Path(loaded['workdir'])
    work.mkdir(parents=True, exist_ok=True)
    bridge = _FakePmBridge(cfg)
    monkeypatch.setattr(telemetry_guard, 'bridge_for', lambda profile: bridge)
    telemetry_guard.begin(loaded, token)
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        ieee=cfg['ieee'], sha256=cfg['sha256'], token=token,
        phase='ota_transfer_ok_postflash_unverified')))
    return loaded, work, bridge


def _metadata_body(cfg, **overrides):
    body = dict(at=dt.datetime.now(dt.timezone.utc).isoformat(), ieee=cfg['ieee'],
        device=cfg['device'], result='metadata_refreshed', error=None, interview_ok=True,
        fresh_inventory_observed=True, live_zdo_after={'role': cfg['postflash_role']},
        after=dict(ieee_address=cfg['ieee'], software_build_id=cfg['postflash_build'],
            type=cfg['postflash_role']))
    body.update(overrides)
    return body


def test_pm_transition_releases_with_final_metadata_not_stale_rejoin(tmp_path, monkeypatch):
    cfg = _pm_transition_profile(tmp_path)
    token = 'campaign-token-1'
    loaded, work, _ = _begin_pm_campaign_guard(tmp_path, monkeypatch, cfg, token)
    released = []

    def recording_release(profile, evidence_path):
        released.append(Path(evidence_path).name)
        return real_telemetry_release(profile, evidence_path)

    monkeypatch.setattr(telemetry_guard, 'release', recording_release)

    def fake_call(cmd):
        name = Path(cmd[2]).name
        if name in ('bseed_z2m_rejoin_window.py', 'bseed_z2m_metadata_refresh.py'):
            out = Path(cmd[cmd.index('--output') + 1])
            if name == 'bseed_z2m_rejoin_window.py':
                # Stale pre-metadata inventory still says Router: the guard
                # must not consume this file for release.
                body = dict(observed_at=_metadata_body(cfg)['at'], ieee=cfg['ieee'],
                    result='postflash_candidate', fresh_inventory_during_window=True,
                    inventory=dict(ieee_address=cfg['ieee'],
                        software_build_id=cfg['postflash_build'], type='Router'),
                    live_node_descriptor={'role': 'EndDevice'})
            else:
                body = _metadata_body(cfg)
            out.write_text(json.dumps(body))
        return 0

    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'transition', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=fake_call):
        with pytest.raises(SystemExit) as done:
            campaign.main()
    assert done.value.code == 0
    assert len(released) == 1 and released[0].startswith('metadata_')
    assert not (work / 'PM_TELEMETRY_GUARD.json').exists()
    assert (work / ('PM_TELEMETRY_RELEASED_' + token + '.json')).exists()


def test_pm_transition_keeps_guard_when_final_metadata_is_stale(tmp_path, monkeypatch):
    cfg = _pm_transition_profile(tmp_path)
    token = 'campaign-token-2'
    loaded, work, _ = _begin_pm_campaign_guard(tmp_path, monkeypatch, cfg, token)
    monkeypatch.setattr(telemetry_guard, 'release', real_telemetry_release)

    def fake_call(cmd):
        if Path(cmd[2]).name == 'bseed_z2m_metadata_refresh.py':
            out = Path(cmd[cmd.index('--output') + 1])
            out.write_text(json.dumps(_metadata_body(cfg, fresh_inventory_observed=False)))
        return 0

    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'transition', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=fake_call):
        with pytest.raises(ValueError, match='fresh nonretained inventory'):
            campaign.main()
    assert (work / 'PM_TELEMETRY_GUARD.json').exists()
    assert not list(work.glob('PM_TELEMETRY_RELEASED_*'))


def test_pm_metadata_resume_releases_matching_guard(tmp_path, monkeypatch):
    cfg = _pm_transition_profile(tmp_path)
    token = 'campaign-token-3'
    loaded, work, _ = _begin_pm_campaign_guard(tmp_path, monkeypatch, cfg, token)
    released = []

    def recording_release(profile, evidence_path):
        released.append(Path(evidence_path).name)
        return real_telemetry_release(profile, evidence_path)

    monkeypatch.setattr(telemetry_guard, 'release', recording_release)

    def fake_call(cmd):
        out = Path(cmd[cmd.index('--output') + 1])
        out.write_text(json.dumps(_metadata_body(cfg, result='metadata_already_correct')))
        return 0

    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'metadata', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=fake_call) as call:
        assert campaign.main() is None
    assert call.call_count == 1
    assert len(released) == 1 and released[0].startswith('metadata_')
    assert (work / ('PM_TELEMETRY_RELEASED_' + token + '.json')).exists()


def test_pm_metadata_resume_keeps_guard_when_release_rejected(tmp_path, monkeypatch):
    cfg = _pm_transition_profile(tmp_path)
    token = 'campaign-token-4'
    loaded, work, _ = _begin_pm_campaign_guard(tmp_path, monkeypatch, cfg, token)
    monkeypatch.setattr(telemetry_guard, 'release', real_telemetry_release)
    before = (work / 'PM_TELEMETRY_GUARD.json').read_bytes()

    def fake_call(cmd):
        out = Path(cmd[cmd.index('--output') + 1])
        out.write_text(json.dumps(_metadata_body(cfg, interview_ok=False)))
        return 0

    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'metadata', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', side_effect=fake_call) as call:
        with pytest.raises(SystemExit) as done:
            campaign.main()
    assert done.value.code == 3
    assert call.call_count == 1  # no OTA submission or retry after release refusal
    assert (work / 'PM_TELEMETRY_GUARD.json').read_bytes() == before
    assert len(list(work.glob('metadata_*.json'))) == 1  # evidence preserved
    assert not list(work.glob('PM_TELEMETRY_RELEASED_*'))


def test_metadata_failure_never_attempts_release(tmp_path, monkeypatch):
    cfg = _pm_transition_profile(tmp_path)
    loaded = campaign.load_profile(_write_profile(tmp_path, cfg, 'pm-meta.json'))
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'metadata', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', return_value=2) as call:
        with patch('bseed_pm_telemetry_guard.release',
                   side_effect=AssertionError('release must not run')):
            with pytest.raises(SystemExit) as done:
                campaign.main()
    assert done.value.code == 2
    assert call.call_count == 1


def test_nonpm_metadata_resume_skips_release(tmp_path, monkeypatch):
    cfg = _nonpm_profile(tmp_path)
    cfg.update(preflash_role='Router', postflash_role='EndDevice')
    loaded = campaign.load_profile(_write_profile(tmp_path, cfg, 'nonpm-meta.json'))
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', loaded['_profile_path'],
                                      '--mode', 'metadata', '--confirm-ieee', cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call', return_value=0) as call:
        with patch('bseed_pm_telemetry_guard.release',
                   side_effect=AssertionError('non-PM must not release')):
            assert campaign.main() is None
    assert call.call_count == 1


def _networked_profile(tmp_path, work_name):
    cfg = profile(tmp_path)
    cfg['workdir'] = str(tmp_path / work_name)
    cfg.update(network_lock_dir=str(tmp_path / 'shared-network-authority'),
               network_id='zigbee-pan-test', network_lock_shared=True)
    return cfg


def test_network_authority_is_shared_across_workdirs_and_outside_repo(tmp_path):
    first = _networked_profile(tmp_path, 'work-a')
    second = _networked_profile(tmp_path, 'work-b')
    path_a = campaign.network_lock_path(first, required=True)
    path_b = campaign.network_lock_path(second, required=True)
    assert path_a == path_b
    assert Path(path_a).parent == Path(first['network_lock_dir']).resolve()
    assert campaign.network_lock_path(profile(tmp_path)) is None
    inside = dict(first, network_lock_dir=str(campaign.ROOT / 'network-authority'))
    with pytest.raises(ValueError, match='outside repository'):
        campaign.network_lock_path(inside, required=True)


def test_postflash_candidate_retains_shared_network_ownership(tmp_path):
    from bseed_network_campaign_lock import acquire
    cfg = _networked_profile(tmp_path, 'work-a')
    work = Path(cfg['workdir'])
    work.mkdir(parents=True)
    network_path = campaign.network_lock_path(cfg, required=True)
    acquire(network_path, network_id=cfg['network_id'], token='token-1',
            device=cfg['device'], ieee=cfg['ieee'], image_sha256=cfg['sha256'])
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        phase='ota_transfer_ok_postflash_unverified', token='token-1')))
    assert campaign.record_postflash_candidate(cfg) is True
    assert network_path.exists()
    record = json.loads((work / 'ACTIVE_LOCK.json').read_text())
    assert record['phase'] == 'postflash_candidate'


def test_acceptance_refuses_ineligible_or_foreign_campaign_state(tmp_path):
    from bseed_network_campaign_lock import acquire, read_lock
    cfg = _networked_profile(tmp_path, 'work-a')
    work = Path(cfg['workdir'])
    work.mkdir(parents=True)
    network_path = campaign.network_lock_path(cfg, required=True)
    acquire(network_path, network_id=cfg['network_id'], token='token-1',
            device=cfg['device'], ieee=cfg['ieee'], image_sha256=cfg['sha256'])
    with pytest.raises(RuntimeError, match='missing'):
        campaign.record_postflash_candidate(cfg)
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        phase='ota_transfer_ok_postflash_unverified')))
    with pytest.raises(RuntimeError, match='token'):
        campaign.record_postflash_candidate(cfg)
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        phase='ota_running', token='token-1')))
    with pytest.raises(RuntimeError, match='eligible'):
        campaign.record_postflash_candidate(cfg)
    (work / 'ACTIVE_LOCK.json').write_text(json.dumps(dict(
        phase='ota_transfer_ok_postflash_unverified', token='foreign')))
    with pytest.raises(RuntimeError, match='mismatch'):
        campaign.record_postflash_candidate(cfg)
    assert network_path.exists()
    assert read_lock(network_path)['token'] == 'token-1'
    # A foreign token leaves both ownership records unchanged.
    record = json.loads((work / 'ACTIVE_LOCK.json').read_text())
    assert record['phase'] == 'ota_transfer_ok_postflash_unverified'
