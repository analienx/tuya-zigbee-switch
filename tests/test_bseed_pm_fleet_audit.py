"""Offline fleet verification tests: no MQTT, SSH, Zigbee or hardware writes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_pm_fleet_audit as fleet


def roster():
    return {'mqtt_config':'private.yml','broker':'127.0.0.1',
            'ssh_host':'test-host','ssh_key':'private-key',
            'targets':[{'name':'TestPMRouter','ieee':'0x0011223344556677',
                        'role':'Router','build':'router-build'}]}


def test_reject_bad_roster_and_duplicate_targets():
    assert len(fleet.validate_roster(roster())['targets']) == 1
    bad=roster();bad['targets'].append(dict(bad['targets'][0]))
    with pytest.raises(ValueError, match='Duplicate'):fleet.validate_roster(bad)
    bad=roster();bad['targets'][0]['role']='unsupported'
    with pytest.raises(ValueError, match='role'):fleet.validate_roster(bad)
    bad=roster();bad['targets'][0]['ieee']='0xwrong'
    with pytest.raises(ValueError, match='IEEE'):fleet.validate_roster(bad)


def test_role_audit_command_never_contains_provision_or_mutation_flags(tmp_path):
    r=roster();target=r['targets'][0]
    cmd=fleet.audit_command(r,target,tmp_path/'evidence.json',85)
    assert Path(cmd[2]).name=='bseed_pm_role_audit.py'
    assert cmd[cmd.index('--expect-role')+1]=='Router'
    assert cmd[cmd.index('--ieee')+1]==target['ieee']
    assert all(flag not in cmd for flag in ('--apply','--allow-configure','--confirm-ieee'))


def test_evidence_cannot_be_accepted_with_failure_or_wrong_identity(tmp_path):
    target=roster()['targets'][0]; evidence=tmp_path/'audit.json'
    payload={'ieee':target['ieee'],'expected_role':'Router',
             'result':'read_only_audit_candidate','issues':[]}
    evidence.write_text(json.dumps(payload),encoding='utf8')
    assert fleet.summarize(target,evidence,2)['result']=='unconfirmed'
    assert fleet.summarize(target,evidence,0)['result']=='read_only_audit_candidate'
    payload['ieee']='0xffffffffffffffff'; evidence.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='identity'):fleet.summarize(target,evidence,0)
    assert fleet.summarize(target,tmp_path/'missing.json',124)['result']=='unconfirmed'


def test_serial_run_records_all_failures_and_never_accepts_fleet(tmp_path):
    r=roster();r['targets'].append({'name':'TestPMClient','ieee':'0xaabbccddeeff1234',
                                   'role':'EndDevice','build':'client-build'})
    config=tmp_path/'roster.json';config.write_text(json.dumps(r),encoding='utf8')
    def emulate(cmd,**_kwargs):
        target=cmd[cmd.index('--ieee')+1];out=Path(cmd[cmd.index('--output')+1])
        result='read_only_audit_candidate' if target==r['targets'][0]['ieee'] else 'unconfirmed'
        out.write_text(json.dumps({'ieee':target,'expected_role':cmd[cmd.index('--expect-role')+1],
                                   'result':result,'issues':[] if result!='unconfirmed' else ['Reporting gap']}))
        return SimpleNamespace(returncode=0 if result!='unconfirmed' else 2)
    with patch.object(fleet.subprocess,'run',side_effect=emulate) as run:
        with pytest.raises(SystemExit) as stopped:fleet.main(['--roster',str(config),
                                                  '--output-dir',str(tmp_path/'evidence')])
    assert stopped.value.code==2 and run.call_count==2
    summary=json.loads((tmp_path/'evidence'/'SUMMARY.json').read_text())
    assert summary['physical_acceptance'] is False
    assert not summary['all_read_only_candidates']
    assert len(summary['targets'])==2
