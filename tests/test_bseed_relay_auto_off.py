"""Automatic relay OFF before a flash, restore after acceptance. GitHub-hosted CI only."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_relay_auto_off as relay  # noqa: E402
import bseed_ota_campaign as campaign  # noqa: E402


class FakeRelay:
    def __init__(self, state='ON', stuck=False):
        self.state, self.stuck, self.writes = state, stuck, []

    def read(self, key):
        return self.state

    def set(self, key, value):
        self.writes.append((key, value))
        if not self.stuck:
            self.state = value
        if self.state != value:
            raise ValueError('Relay did not switch %s' % value)
        return value

    def close(self):
        pass


def profile(tmp_path, **changes):
    data = dict(device='Cabinet', ieee='0x0011223344556677', workdir=str(tmp_path / 'work'),
                auto_relay_off=True, require_pm=True, non_pm=False, expect_relay='OFF',
                relay_get_key='state_relay', manufacturer='b28wrpvx', model='TS011F-BS-PM',
                pm_preflash_load_proof='meter')
    data.update(changes)
    return data


def test_on_relay_is_switched_off_recorded_once_and_restored_after_acceptance(tmp_path):
    p, fake = profile(tmp_path), FakeRelay('ON')
    record = relay.switch_off_before_flash(p, fake)
    assert record['preflash'] == 'ON' and fake.state == 'OFF'
    assert fake.writes == [('state_relay', 'OFF')]
    # A resumed campaign keeps the first truth even though the relay now reads OFF.
    assert relay.switch_off_before_flash(p, fake)['preflash'] == 'ON'
    relay.restore_after_acceptance(p, fake)
    assert fake.state == 'ON' and fake.writes[-1] == ('state_relay', 'ON')
    saved = json.loads(relay.record_path(p).read_text())
    assert saved['restored'] is True
    relay.restore_after_acceptance(p, fake)  # idempotent: no second write
    assert fake.writes.count(('state_relay', 'ON')) == 1


def test_off_relay_is_never_written_and_stays_off(tmp_path):
    p, fake = profile(tmp_path), FakeRelay('OFF')
    relay.switch_off_before_flash(p, fake)
    relay.restore_after_acceptance(p, fake)
    assert fake.writes == [] and fake.state == 'OFF'


def test_relay_that_does_not_switch_stops_the_flash(tmp_path):
    with pytest.raises(ValueError, match='did not switch OFF'):
        relay.switch_off_before_flash(profile(tmp_path), FakeRelay('ON', stuck=True))


def test_rejoin_reasserts_off_on_the_custom_key_until_acceptance(tmp_path):
    p, fake = profile(tmp_path), FakeRelay('ON')
    assert relay.reassert_off_after_rejoin(p, fake) is None  # no record: nothing to hold
    relay.switch_off_before_flash(p, fake)
    fake.state = 'ON'  # first boot without saved relay state
    relay.reassert_off_after_rejoin(p, fake)
    assert fake.state == 'OFF' and fake.writes[-1] == ('state_relay', 'OFF')


def test_record_from_another_target_is_refused(tmp_path):
    p, fake = profile(tmp_path), FakeRelay('ON')
    relay.switch_off_before_flash(p, fake)
    with pytest.raises(ValueError, match='different target'):
        relay.switch_off_before_flash(dict(p, ieee='0x00112233445566ff'), fake)


@pytest.mark.parametrize('changes, match', [
    ({'auto_relay_off': 'yes'}, 'explicit Boolean'),
    ({'require_pm': False, 'non_pm': True}, 'PM board'),
    ({'pm_preflash_load_proof': 'physically_unloaded'}, 'replaces the physical'),
    ({'expect_relay': 'ON'}, 'expect_relay=OFF'),
    ({'manufacturer': '_TZ3000_b28wrpvx', 'model': 'TS011F'}, 'custom PM firmware'),
])
def test_profile_shape(tmp_path, changes, match):
    with pytest.raises(ValueError, match=match):
        relay.validate(profile(tmp_path, **changes))


def test_disabled_by_default(tmp_path):
    assert relay.validate(profile(tmp_path, auto_relay_off=None)) is False
    assert relay.validate(profile(tmp_path)) is True


def test_campaign_switches_off_and_checks_before_flash_and_restores_on_acceptance(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(relay, 'switch_off_before_flash', lambda p: calls.append('off'))
    monkeypatch.setattr(relay, 'restore_after_acceptance', lambda p: calls.append('restore'))
    monkeypatch.setattr(campaign, 'runner_args', lambda p, mode, **k: ['runner', mode])
    monkeypatch.setattr(campaign.subprocess, 'call', lambda cmd: calls.append(tuple(cmd)) or 0)
    monkeypatch.setattr(campaign, 'record_postflash_candidate', lambda p: calls.append('candidate'))
    p = profile(tmp_path)
    assert campaign.auto_relay_off_then_check(p) == 0
    campaign.accept_candidate(p)
    assert calls == ['off', ('runner', 'check'), 'candidate', 'restore']
    calls.clear()
    assert campaign.auto_relay_off_then_check(dict(p, auto_relay_off=False)) == 0
    campaign.accept_candidate(dict(p, auto_relay_off=False))
    assert calls == ['candidate']


def test_auto_off_runs_before_the_snapshot_and_the_flash_in_both_modes():
    source = (ROOT / 'helper_scripts/bseed_ota_campaign.py').read_text(encoding='utf8')
    for marker in ("print('ONE_DEVICE_ROLE_TRANSITION'", "print('EXPLICIT_FLASH_TARGET'"):
        flash = source.index(marker)
        off = source.rindex('auto_relay_off_then_check(profile)', 0, flash)
        assert source.rindex('take_state_snapshot(profile)', 0, flash) > off
    # Every candidate path goes through accept_candidate (the only direct call is inside it).
    assert source.count('record_postflash_candidate(profile)\n') == 1
    assert source.count('accept_candidate(profile)\n') >= 5
