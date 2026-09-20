"""Serial read-only audits of explicitly identified PM Client/Router canaries.

Never flashes, switches, rejoins, reconfigures or accepts a firmware image.
Store roster and evidence privately outside this repository.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
ROLES = {'Router', 'EndDevice'}


def validate_roster(roster):
    if not isinstance(roster, dict) or not isinstance(roster.get('targets'), list):
        raise ValueError('Private roster requires targets array')
    if not 1 <= len(roster['targets']) <= 24:
        raise ValueError('Limit serial verification to 1..24 targets')
    required = ('mqtt_config', 'broker', 'ssh_host', 'ssh_key')
    if any(not roster.get(k) for k in required):
        raise ValueError('Missing private connection configuration')
    names, ieee_ids = set(), set()
    for target in roster['targets']:
        if not isinstance(target, dict): raise ValueError('Malformed target')
        name, ieee = target.get('name'), target.get('ieee')
        if not isinstance(name, str) or not name or '/' in name:
            raise ValueError('Invalid friendly name')
        if not isinstance(ieee, str) or len(ieee) != 18 or not ieee.startswith('0x'):
            raise ValueError('Exact IEEE required for each target')
        if name in names or ieee in ieee_ids:
            raise ValueError('Duplicate friendly name or IEEE')
        if target.get('role') not in ROLES or not target.get('build'):
            raise ValueError('Expected role and exact build required')
        if 'baseline' in target and not Path(target['baseline']).is_file():
            raise ValueError('Missing private baseline for ' + name)
        names.add(name); ieee_ids.add(ieee)
    return roster


def audit_command(roster, target, evidence, seconds):
    """No mutation flags exist on the invoked role-audit entry point."""
    cmd = [sys.executable, '-u', str(ROOT/'helper_scripts/bseed_pm_role_audit.py'),
           '--device', target['name'], '--ieee', target['ieee'],
           '--expect-role', target['role'], '--expect-build', target['build'],
           '--mqtt-config', roster['mqtt_config'], '--broker', roster['broker'],
           '--ssh-host', roster['ssh_host'], '--ssh-key', roster['ssh_key'],
           '--output', str(evidence), '--observe-seconds', str(seconds)]
    if target.get('baseline'):
        cmd.extend(['--baseline', str(target['baseline'])])
    return cmd


def summarize(target, output, process_code):
    if not output.is_file():
        return {'name': target['name'], 'role': target['role'],
                'result': 'unconfirmed', 'issues': ['No target evidence written'],
                'exit_code': process_code}
    result = json.loads(output.read_text(encoding='utf8'))
    if result.get('ieee') != target['ieee'] or result.get('expected_role') != target['role']:
        raise ValueError('Evidence identity/role mismatch')
    accepted = process_code == 0 and result.get('result') == 'read_only_audit_candidate'
    return {'name': target['name'], 'role': target['role'],
            'result': result.get('result') if accepted else 'unconfirmed',
            'issues': result.get('issues', []), 'missing_reporting': result.get('missing_reporting', []),
            'exit_code': process_code, 'evidence': str(output)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--roster', required=True, help='Private JSON roster outside git')
    p.add_argument('--output-dir', required=True, help='New private evidence directory outside git')
    p.add_argument('--observe-seconds', type=int, default=85)
    args = p.parse_args(argv)
    if not 60 <= args.observe_seconds <= 420:
        raise ValueError('Bounded observation must be 60..420 seconds')
    roster_path = Path(args.roster).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if roster_path.is_relative_to(ROOT) or output_dir.is_relative_to(ROOT):
        raise ValueError('Roster and evidence must stay outside repository')
    if output_dir.exists():
        raise ValueError('Refuse to reuse an existing evidence directory')
    roster = validate_roster(json.loads(roster_path.read_text(encoding='utf8')))
    output_dir.mkdir(parents=True, exist_ok=False)
    summary = {'observed_at': dt.datetime.now().astimezone().isoformat(),
               'audit_only': True, 'physical_acceptance': False,
               'targets': [], 'all_read_only_candidates': False}
    try:
        for target in roster['targets']:
            output = output_dir / ('audit_' + uuid.uuid4().hex + '.json')
            cmd = audit_command(roster, target, output, args.observe_seconds)
            print('AUDIT_TARGET', target['name'], target['role'], flush=True)
            try:
                outcome = subprocess.run(cmd, timeout=args.observe_seconds + 80,
                                         check=False, capture_output=True, text=True)
                code = outcome.returncode
            except subprocess.TimeoutExpired:
                code = 124
            result = summarize(target, output, code)
            summary['targets'].append(result)
            print('AUDIT_RESULT', result['name'], result['result'],
                  result.get('issues', []), flush=True)
        summary['all_read_only_candidates'] = bool(summary['targets']) and all(
            r['result'] == 'read_only_audit_candidate' for r in summary['targets'])
    finally:
        (output_dir / 'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
        print('PRIVATE_FLEET_EVIDENCE', output_dir, flush=True)
    if not summary['all_read_only_candidates']:
        raise SystemExit(2)


if __name__ == '__main__': main()
