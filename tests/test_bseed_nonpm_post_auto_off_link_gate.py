"""Regression: final relay-OFF OTA check must be followed by fresh link proof."""
import inspect
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_campaign  # noqa: E402


def test_nonpm_gate_runs_after_final_check():
    profile = {'non_pm': True, '_profile_path': '/private/nonpm.json'}
    calls = []
    def fake_runner(argv):
        calls.append(argv)
        return 0
    assert bseed_ota_campaign.verify_nonpm_link_after_final_check(profile, run=fake_runner) == 0
    assert len(calls) == 1
    assert calls[0][-2:] == ['--profile', '/private/nonpm.json']
    assert 'bseed_nonpm_link_gate.py' in calls[0][2]


def test_failure_is_not_ignored():
    profile = {'non_pm': True, '_profile_path': '/private/nonpm.json'}
    assert bseed_ota_campaign.verify_nonpm_link_after_final_check(
        profile, run=lambda _args: 19) == 19


def test_pm_does_not_run_nonpm_gate():
    def fail_if_called(_args):
        raise AssertionError('Never run nonPM on PM socket')
    assert bseed_ota_campaign.verify_nonpm_link_after_final_check(
        {'non_pm': False}, run=fail_if_called) == 0


def test_flash_flow_inserts_gate_between_last_check_and_snapshot():
    source = inspect.getsource(bseed_ota_campaign.main)
    block = source[source.rindex("    if args.mode == 'flash':"):]
    check = block.index('checked = auto_relay_off_then_check(profile)')
    link = block.index('link_status = verify_nonpm_link_after_final_check(profile)')
    proof = block.index('confirmed, relay_record = load_confirmation(profile')
    snapshot = block.index('snapshot_status = take_state_snapshot(profile)')
    flash = block.index("runner_args(profile, 'flash'")
    assert check < link < proof < snapshot < flash
