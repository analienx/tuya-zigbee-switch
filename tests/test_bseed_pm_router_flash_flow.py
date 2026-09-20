"""Offline Router PM OTA gate; no network/relay/flash operations."""
import json,sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
import bseed_ota_campaign as campaign
from test_bseed_ota_campaign import _router_candidate_profile

def test_validated_router_flash_never_runs_client_provision(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    path=tmp_path/'router_profile.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash','--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=0) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==0
    commands=[x.args[0] for x in call.call_args_list]
    assert [Path(x[2]).name for x in commands]==[
        'bseed_targeted_z2m_ota.py','bseed_z2m_postota_reinterview.py',
        'bseed_z2m_postflash_verify.py','bseed_pm_role_audit.py']
    assert '--require-pm' in commands[2] and '--apply' not in str(commands)
    assert '--relay-get-key' in commands[0] and 'state_relay' in commands[0]

def test_router_flash_never_retries_or_provisions_after_failed_transport(tmp_path,monkeypatch):
    cfg=_router_candidate_profile(tmp_path)
    path=tmp_path/'router_profile.json';path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['campaign','--profile',str(path),'--mode','flash','--confirm-ieee',cfg['ieee']])
    with patch('bseed_ota_campaign.subprocess.call',return_value=2) as call:
        with pytest.raises(SystemExit) as done:campaign.main()
    assert done.value.code==2 and len(call.call_args_list)==1
