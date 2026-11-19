"""Converter deploy gates. GitHub-hosted CI only; no network or SSH."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import z2m_converter_deploy as deploy  # noqa: E402


def artifact(tmp_path, data, listed=None):
    d = tmp_path / 'artifact'
    d.mkdir()
    (d / 'switch_custom.js').write_bytes(data)
    digest = listed or hashlib.sha256(data).hexdigest()
    (d / 'SHA256SUMS').write_text(digest + '  converter-canary/switch_custom.js\n'
                                  'ffff  converter-canary/switch_custom_z2m_v1.js\n')
    return d


def test_artifact_must_match_ci_sums_and_committed_converter(tmp_path):
    committed = tmp_path / 'committed.js'
    committed.write_bytes(b'converter')
    assert deploy.artifact_sha(artifact(tmp_path, b'converter'), committed) == hashlib.sha256(b'converter').hexdigest()
    with pytest.raises(deploy.Refused, match='SHA256SUMS'):
        deploy.artifact_sha(artifact(tmp_path / 'a', b'converter', listed='0' * 64), committed)
    with pytest.raises(deploy.Refused, match='committed'):
        deploy.artifact_sha(artifact(tmp_path / 'b', b'other'), committed)


def test_pins_come_from_the_ci_converter_runtime():
    pins = deploy.pinned_versions()
    package = json.loads((ROOT / 'tests/zhc_runtime/package.json').read_text())['dependencies']
    assert pins == {name: package[name] for name in deploy.PINNED}
    assert deploy.version_drift(pins, dict(pins)) == {}
    drift = deploy.version_drift(pins, {**pins, 'zigbee-herdsman-converters': '0.0.1'})
    assert list(drift) == ['zigbee-herdsman-converters']


def test_any_bseed_lease_blocks_a_restart(tmp_path):
    assert deploy.active_leases(tmp_path) == []
    (tmp_path / 'bseed-zigbee-abc.slot-0xa4c1380011223344.json').write_text('{}')
    (tmp_path / 'release-x.request.json').write_text('{}')
    assert deploy.active_leases(tmp_path) == ['bseed-zigbee-abc.slot-0xa4c1380011223344.json']


OLD = "info: Starting Zigbee2MQTT version 2.14.2\ninfo: Loaded external converter 'switch_custom.js'.\ninfo: Zigbee2MQTT started!\n"


@pytest.mark.parametrize('new, verdict', [
    ("info: Starting Zigbee2MQTT version 2.14.2\ninfo: Loaded external converter 'switch_custom.js'.\n"
     "info: Zigbee2MQTT started!\n", 'ok'),
    ("info: Starting Zigbee2MQTT version 2.14.2\ninfo: Loaded external converter 'switch_custom.js'.\n", 'pending'),
    ("info: Starting Zigbee2MQTT version 2.14.2\nerror: Failed to load external converter 'switch_custom.js' x\n"
     "info: Zigbee2MQTT started!\n", 'converter_failed'),
    ("info: Starting Zigbee2MQTT version 2.14.2\ninfo: Zigbee2MQTT started!\n", 'converter_missing'),
])
def test_only_the_latest_start_is_judged(new, verdict):
    assert deploy.start_log_verdict(OLD + new) == verdict
    assert deploy.start_log_verdict('nothing yet') == 'no_start'


def test_refuses_before_touching_the_host_while_an_ota_runs(tmp_path, monkeypatch, capsys):
    data = (ROOT / 'zigbee2mqtt/converters/switch_custom.js').read_bytes()
    locks = tmp_path / 'locks'
    locks.mkdir()
    (locks / 'bseed-zigbee-abc.json').write_text('{}')
    site = tmp_path / 'site.json'
    site.write_text(json.dumps({'network_lock_dir': str(locks), 'ha_ssh_alias': 'ha.invalid'}))
    monkeypatch.setattr(deploy.Host, 'run', lambda *a, **k: pytest.fail('host touched'))
    assert deploy.main(['--artifact-dir', str(artifact(tmp_path, data)), '--site', str(site), '--apply']) == 2
    assert 'OTA lease held' in capsys.readouterr().out
