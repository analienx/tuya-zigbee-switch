"""Offline tests of the standard post-update state refresh; never connects to a live network."""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_z2m_state_refresh as refresh  # noqa: E402

BUILD = '1.2.5-bseedr22'
# Table Left after the wired r22 restore: Zigbee2MQTT kept the 2026-11-06 sample.
STALE = {'state': 'OFF', 'state_relay': 'ON', 'relay_physical_mode': 'follow_state', 'power': 2,
         'current': 0.08, 'voltage': 251.1, 'energy': 0.47,
         'bseed_pm_sample_time_ms': 1793952755385, 'bseed_pm_sample_power_w': 2}
REQUEST_MS = 1794594800000


def test_repeated_cached_power_is_not_a_fresh_reading():
    # Zigbee2MQTT republishes the whole cached state with every answer.
    keys = refresh.refresh_keys('state_relay', True)
    result = refresh.assess_refresh(STALE, dict(STALE), keys=keys, expected_build=BUILD,
                                    baseline_ms=STALE['bseed_pm_sample_time_ms'], request_ms=REQUEST_MS,
                                    received_ms=REQUEST_MS + 3000, require_pm=True)
    assert result['freshPmSample'] is None
    assert any('newly stamped ZCL power' in issue for issue in result['issues'])
    assert result['physicalAcceptance'] is False


def test_new_stamp_replaces_stale_power_and_records_the_change():
    keys = refresh.refresh_keys('state_relay', True)
    fresh = dict(STALE, power=0, current=0, voltage=233.4, energy=0.47,
                 bseed_pm_sample_time_ms=REQUEST_MS + 900, bseed_pm_sample_power_w=0)
    result = refresh.assess_refresh(STALE, fresh, keys=keys, expected_build=BUILD,
                                    converter_ok=True, baseline_ms=STALE['bseed_pm_sample_time_ms'],
                                    request_ms=REQUEST_MS, received_ms=REQUEST_MS + 3000, require_pm=True)
    assert result['issues'] == []
    assert result['freshPmSample'] == {'power': 0, 'sample_time_ms': REQUEST_MS + 900}
    assert result['fields']['power'] == {'cached': 2, 'fresh': 0, 'reported': True, 'changed': True}
    assert result['converterKnowsBuild'] is True


def test_stamp_from_before_the_request_or_far_future_is_refused():
    for stamp in (REQUEST_MS - 60000, REQUEST_MS + 3_600_000):
        state = dict(STALE, bseed_pm_sample_time_ms=stamp)
        assert refresh.fresh_pm_sample(state, baseline_ms=1, request_ms=REQUEST_MS,
                                       received_ms=REQUEST_MS + 3000) is None


def test_unanswered_field_is_unconfirmed_not_shown_as_current():
    result = refresh.assess_refresh(STALE, {'state_relay': 'ON'}, keys=refresh.refresh_keys('state_relay', False),
                                    expected_build=BUILD)
    assert result['issues'] == ['no fresh value for relay_physical_mode']
    assert result['fields']['relay_physical_mode']['reported'] is False


def test_deployed_converter_without_the_build_is_reported():
    # The converter deployed on 2026-11-13 listed builds only up to r21.
    deployed = 'const knownBuilds = new Set(["1.2.5-bseedr20", "1.2.5-bseedr21"]);'
    assert refresh.converter_known_builds(deployed) == {'1.2.5-bseedr20', '1.2.5-bseedr21'}
    assert refresh.converter_accepts_build(deployed, '1.2.5-bseedr21')
    ok = refresh.converter_accepts_build(deployed, BUILD)
    result = refresh.assess_refresh({}, {'state': 'ON', 'relay_physical_mode': 'x'},
                                    keys=refresh.refresh_keys('state', False),
                                    expected_build=BUILD, converter_ok=ok)
    assert result['converterKnowsBuild'] is False and result['cachedBaseline'] is False
    assert result['fields']['state'] == {'cached': 'unknown', 'fresh': 'ON', 'reported': True, 'changed': None}
    assert any('does not recognise ' + BUILD in issue for issue in result['issues'])


def test_repository_converter_recognises_future_builds_without_a_list():
    text = (ROOT / 'zigbee2mqtt' / 'converters' / 'switch_custom.js').read_text(encoding='utf-8')
    for build in (BUILD, '1.2.5-bseedr23', '1.2.5-bseedcli40'):
        assert refresh.converter_accepts_build(text, build), build


def test_ota_check_is_version_check_only():
    assert refresh.ota_check_result({'status': 'ok', 'data': {'id': 'X', 'update_available': False}}, 'X') == {
        'checked': True, 'updateAvailable': False}
    assert refresh.ota_check_result({'status': 'ok', 'data': {'id': 'Y'}}, 'X')['checked'] is False
    assert refresh.ota_check_result(None, 'X')['checked'] is False
    source = (ROOT / 'helper_scripts' / 'bseed_z2m_state_refresh.py').read_text(encoding='utf-8')
    assert 'ota_update/check' in source and 'ota_update/update' not in source


class FakeClient:
    def __init__(self, inbox, changed, replies):
        self.inbox, self.changed, self.replies, self.sent = inbox, changed, replies, []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, retain))
        if topic.endswith('/get') and self.replies:
            self.inbox.append(self.replies.pop(0))
            self.changed.set()
        return type('Sent', (), {'wait_for_publish': lambda *_: True})()


def test_run_refresh_reads_relay_and_both_pm_clusters_without_writes():
    inbox, changed = refresh.Inbox(), threading.Event()
    client = FakeClient(inbox, changed, [{'state_relay': 'ON', 'relay_physical_mode': 'follow_state'},
                                         {'power': 0, 'bseed_pm_sample_time_ms': 5, 'bseed_pm_sample_power_w': 0},
                                         {'energy': 0.47}])
    fresh, *_ = refresh.run_refresh(client, 'zigbee2mqtt', 'Dev', keys=refresh.refresh_keys('state_relay', True),
                                    cached=STALE, request_pm=True, observe_seconds=0.2, inbox=inbox,
                                    changed=changed)
    topics = [topic for topic, _payload, _retain in client.sent]
    assert topics == ['zigbee2mqtt/Dev/get'] * 3
    assert not any(retain for *_rest, retain in client.sent)
    assert fresh['state_relay'] == 'ON' and fresh['power'] == 0 and fresh['energy'] == 0.47


def test_postflash_verifier_runs_the_refresh_by_default():
    source = (ROOT / 'helper_scripts' / 'bseed_z2m_postflash_verify.py').read_text(encoding='utf-8')
    assert 'if not args.skip_state_refresh:' in source and "'state_refresh': refresh" in source


@pytest.mark.parametrize('key', ['relay', '', None])
def test_unknown_relay_key_is_refused(key):
    with pytest.raises(ValueError):
        refresh.refresh_keys(key, False)
