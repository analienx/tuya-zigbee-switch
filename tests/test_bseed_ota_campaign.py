"""Offline tests of profile orchestration; no live MQTT, HTTP or firmware changes."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_campaign as campaign


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


def test_private_index_stages_exact_single_image(tmp_path):
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
