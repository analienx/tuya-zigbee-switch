"""Offline non-PM reachability evidence and OTA sequencing guards."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'helper_scripts'))
from bseed_nonpm_link_gate import verify_record
from bseed_ota_campaign import runner_args, load_profile


def profile():
    return dict(device='BedroomSocketCabinetRight',ieee='0xa4c13824a7005afb',
        sha256='a'*64,preflash_build='1.1.2-bseedcli4',
        preflash_relay_physical_mode='follow_state',manufacturer='o1jzcxou',
        model='TS011F-BS',preflash_role='EndDevice',postflash_role='EndDevice',
        require_pm=False,non_pm=True)


def record():
    p=profile()
    return dict(schema=1,passed=True,device=p['device'],ieee=p['ieee'],
        image_sha256=p['sha256'],build=p['preflash_build'],
        samples=[dict(valid=True,requested_at=x,latency_s=1.1) for x in (800,830,860)],
        completed_at=862)


def test_three_separated_fresh_responses_are_required():
    p=profile();r=record()
    assert verify_record(r,p,now=870,after=799)
    for bad in ([],r['samples'][:2],r['samples'][:1]+r['samples'][1:2]*2):
        changed=dict(r,samples=bad)
        with pytest.raises(AssertionError): verify_record(changed,p,now=870)
    r['samples'][1]['valid']=False
    with pytest.raises(AssertionError,match='Unresponsive'): verify_record(r,p,now=870)


def test_reject_wrong_target_build_firmware_age_or_reused_precheck_gate():
    p=profile();r=record()
    for field,val in [('device','Neighbor'),('ieee','0xBAD'),('build','old'),
                      ('image_sha256','b'*64),('passed',False),('schema',3)]:
        with pytest.raises(AssertionError): verify_record(dict(r,**{field:val}),p,now=870)
    with pytest.raises(AssertionError,match='expired'): verify_record(r,p,now=1500)
    with pytest.raises(AssertionError,match='NEW link gate'): verify_record(r,p,now=870,after=801)
    changed=record();changed['samples'][1]['requested_at']=810
    with pytest.raises(AssertionError,match='too close'): verify_record(changed,p,now=870)


def test_campaign_only_gates_opted_in_client_and_pins_runtime_baseline(tmp_path):
    p=profile()
    required=('image','url','mqtt_config','broker','workdir','manufacturer_code',
              'image_type','file_version','expect_relay','index_url','postflash_build')
    p.update({k:'test' for k in required})
    cmd=runner_args(p,'check')
    assert cmd[cmd.index('--preflash-build')+1]=='1.1.2-bseedcli4'
    assert cmd[cmd.index('--preflash-relay-physical-mode')+1]=='follow_state'
    assert '--non-pm' in cmd
    p['non_pm']=False
    assert '--preflash-build' not in runner_args(p,'check')
    p['non_pm']=True;p['require_pm']=True
    filename=tmp_path/'profile.json'
    p.update(workdir=str(tmp_path/'work'),index_output=str(tmp_path/'index.json'),template_index=str(tmp_path/'template.json'))
    filename.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='require_pm=false'): load_profile(filename)


def test_link_gate_never_issues_ota_relay_set_join_or_interview():
    src=(ROOT/'helper_scripts/bseed_nonpm_link_gate.py').read_text()
    assert "'/get'" in src
    for forbidden in ('/set', 'ota_update/', 'permit_join', 'device/interview',
                      'device/remove', 'factory_reset', 'relay/set'):
        if forbidden == 'permit_join':
            assert "state['info'].get('permit_join') is False" in src
        else:
            assert forbidden not in src
    runner=(ROOT/'helper_scripts/bseed_targeted_z2m_ota.py').read_text()
    assert "if args.non_pm and args.mode in ('check', 'flash')" in runner
    assert "after = json.loads(checked.read_text(encoding='utf8'))['timestamp']" in runner


def test_new_ota_check_archives_prior_permission_even_if_new_check_fails(tmp_path):
    from bseed_targeted_z2m_ota import archive_prior_check
    prior = tmp_path/'LAST_CHECK.json'
    prior.write_text('{"status":"previous_success"}')
    archived = archive_prior_check(tmp_path)
    assert archived is not None and archived.read_text() == '{"status":"previous_success"}'
    assert not prior.exists()
    assert archive_prior_check(tmp_path) is None
    assert archived.is_file()


def test_gate_interruption_invalidates_previous_evidence_before_network():
    src=(ROOT/'helper_scripts/bseed_nonpm_link_gate.py').read_text()
    assert "'last_error':'link_gate_started_not_completed'" in src
    assert src.index("'last_error':'link_gate_started_not_completed'") < src.index('client.connect(')


def test_qualify_orchestrates_only_three_readonly_stages(tmp_path,monkeypatch):
    from tests.test_bseed_ota_campaign import profile as stock_profile
    import bseed_ota_campaign as campaign
    p=stock_profile(tmp_path)
    p.update(device='BedroomSocketCabinetRight',ieee='0xa4c13824a7005afb',
        manufacturer='o1jzcxou',model='TS011F-BS',preflash_role='EndDevice',
        postflash_role='EndDevice',preflash_build='1.1.2-bseedcli4',
        preflash_relay_physical_mode='follow_state',non_pm=True,require_pm=False)
    work=Path(p['workdir']);work.mkdir()
    (work/'LAST_CHECK.json').write_text('{"previous":"must_archive"}')
    private=tmp_path/'client.json';private.write_text(json.dumps(p))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(private),'--mode','qualify'])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as call:
        campaign.main()
    cmds=[x.args[0] for x in call.call_args_list]
    assert [Path(c[2]).name for c in cmds]==[
        'bseed_nonpm_link_gate.py','bseed_targeted_z2m_ota.py','bseed_nonpm_link_gate.py']
    assert cmds[1][cmds[1].index('--mode')+1]=='check'
    assert '--mode' not in cmds[0] and '--mode' not in cmds[2]
    assert not (work/'LAST_CHECK.json').exists()
    assert len(list(work.glob('CHECK_ARCHIVE_*.json')))==1


def test_qualify_stops_on_first_failed_gate_or_ota_check(tmp_path,monkeypatch):
    from tests.test_bseed_ota_campaign import profile as stock_profile
    import bseed_ota_campaign as campaign
    p=stock_profile(tmp_path)
    p.update(manufacturer='o1jzcxou',model='TS011F-BS',preflash_role='EndDevice',
       postflash_role='EndDevice',preflash_build='1.1.2-bseedcli4',
       preflash_relay_physical_mode='follow_state',non_pm=True,require_pm=False)
    private=tmp_path/'client.json';private.write_text(json.dumps(p))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(private),'--mode','qualify'])
    for results,count in [([2],1),([0,2],2)]:
        with patch('bseed_ota_campaign.subprocess.call',side_effect=results) as call:
            with pytest.raises(SystemExit) as stopped: campaign.main()
        assert stopped.value.code==2 and call.call_count==count
    p['require_pm']=True; private.write_text(json.dumps(p))
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError,match='require_pm=false'): campaign.main()
        call.assert_not_called()
