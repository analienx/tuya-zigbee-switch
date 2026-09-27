"""Role transition uses its own recovery contract and propagates confirmations."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from test_bseed_nonpm_recovery_gate import fixture
from test_bseed_ota_campaign import profile as base_profile
from bseed_nonpm_recovery_gate import verify_recovery, verify_transition_recovery
from bseed_targeted_z2m_ota import validate_metering_preflight
import bseed_ota_campaign as campaign


def test_cross_role_contract_keeps_same_role_gate_strict(tmp_path):
    p, _, _, _ = fixture(tmp_path, preflash_build='1.1.3-bseedv8')
    p['preflash_role'] = 'Router'
    with pytest.raises(ValueError, match='role mismatch'):
        verify_recovery(p, confirm_unloaded=True)
    assert verify_transition_recovery(p, confirm_unloaded=True)
    with pytest.raises(ValueError, match='not explicitly confirmed'):
        verify_transition_recovery(p)
    with pytest.raises(ValueError, match='waiver'):
        verify_transition_recovery(p, confirm_unloaded=True, accept_nonrecoverable_ota=True)


def test_router_no_meter_exception_requires_verified_transition_context():
    args = dict(non_pm=True, model='TS011F-BS', manufacturer='o1jzcxou', role='Router', max_reported_watts=1)
    with pytest.raises(AssertionError): validate_metering_preflight({}, **args)
    assert validate_metering_preflight({}, **args, nonpm_router_transition=True) is None


def test_canonical_transition_reaches_submission_only_with_exact_recovery(tmp_path, monkeypatch):
    p, _, _, _ = fixture(tmp_path, preflash_build='1.1.3-bseedv8')
    cfg = base_profile(tmp_path)
    cfg.update(p, preflash_role='Router', postflash_role='EndDevice', require_pm=False,
               preflash_relay_physical_mode='follow_state', join_via='VerifiedParent')
    path = tmp_path / 'transition.json'; path.write_text(json.dumps(cfg))
    argv = ['campaign', '--profile', str(path), '--mode', 'transition', '--confirm-ieee', cfg['ieee']]
    monkeypatch.setattr(sys, 'argv', argv)
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError, match='not explicitly confirmed'): campaign.main()
        call.assert_not_called()
    monkeypatch.setattr(sys, 'argv', argv + ['--confirm-load-unplugged'])
    with patch('bseed_ota_campaign.subprocess.call', return_value=0) as call:
        with pytest.raises(SystemExit) as done: campaign.main()
    assert done.value.code == 0
    first = call.call_args_list[0].args[0]
    assert '--confirm-load-unplugged' in first and '--campaign-profile' in first
    assert first[first.index('--role') + 1] == 'Router'
    assert first[first.index('--hardware-evidence') + 1] == cfg['recovery_evidence']
    cfg['ieee'] = '0x0000000000000000'; path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys, 'argv', argv[:-1] + [cfg['ieee'], '--confirm-load-unplugged'])
    with patch('bseed_ota_campaign.subprocess.call') as call:
        with pytest.raises(ValueError, match='schema/IEEE'): campaign.main()
        call.assert_not_called()
