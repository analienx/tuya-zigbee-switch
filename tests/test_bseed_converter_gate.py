"""Mandatory live deployed-converter gate for PM campaigns. GitHub-hosted CI only."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import bseed_converter_gate as gate  # noqa: E402
import bseed_converter_release  # noqa: E402
from bseed_converter_gate import require_deployed_converter as real_gate  # noqa: E402  (before conftest stub)
import bseed_ota_campaign as campaign  # noqa: E402

FAMILY = ('const fixedScaleFamily = /^1\\.2\\.5-bseed(r|cli)(\\d+)$/;\n'
          'const fixedScaleFirst = {r: 7, cli: 6};\n'
          'const fixedScaleExcluded = new Set(["1.2.5-bseedr10", "1.2.5-bseedcli7"]);\n'
          + '\n'.join(bseed_converter_release.FAMILY_LOGIC_TOKENS))
LISTED = 'const knownBuilds = new Set([\n    "1.2.5-bseedcli25", "1.2.5-bseedr22",\n]);\n'


def profile(tmp_path, build='1.2.5-bseedcli26', **changes):
    data = dict(require_pm=True, postflash_build=build, pm_ssh_host='ha.invalid',
                pm_ssh_key=str(tmp_path / 'key'), workdir=str(tmp_path / 'work'))
    data.update(changes)
    return data


def serve(monkeypatch, text):
    calls = []
    def fetch(host, user, key, path):
        calls.append((host, user, path))
        if isinstance(text, Exception): raise text
        return text
    monkeypatch.setattr(gate, 'fetch_deployed_converter', fetch)
    return calls


def test_family_converter_accepts_future_builds_and_keeps_a_private_copy(tmp_path, monkeypatch):
    calls = serve(monkeypatch, FAMILY)
    for build in ('1.2.5-bseedcli26', '1.2.5-bseedr23', '1.2.5-bseedcli99'):
        record = real_gate(profile(tmp_path, build))
        assert record['accepted'] is True and record['build'] == build
    assert calls[0] == ('ha.invalid', 'root', gate.DEFAULT_CONVERTER_PATH)
    work = tmp_path / 'work'
    assert (work / gate.COPY_NAME).read_text(encoding='utf8') == FAMILY
    assert json.loads((work / gate.RECORD_NAME).read_text())['accepted'] is True
    assert gate.converter_copy(profile(tmp_path)) == str(work / gate.COPY_NAME)


@pytest.mark.parametrize('text, build', [(LISTED, '1.2.5-bseedcli26'),  # 2026-11-18 incident
                                         (FAMILY, '1.2.5-bseedcli7'),   # excluded scale contract
                                         ('', '1.2.5-bseedcli26')])
def test_unrecognised_build_is_refused_with_evidence(tmp_path, monkeypatch, text, build):
    serve(monkeypatch, text)
    with pytest.raises(ValueError, match='does not recognise ' + build.replace('.', r'\.')):
        real_gate(profile(tmp_path, build))
    assert json.loads((tmp_path / 'work' / gate.RECORD_NAME).read_text())['accepted'] is False


def test_listed_older_converter_still_accepts_listed_build(tmp_path, monkeypatch):
    serve(monkeypatch, LISTED)
    assert real_gate(profile(tmp_path, '1.2.5-bseedcli25'))['accepted'] is True


def test_unreadable_converter_or_missing_access_fails_closed(tmp_path, monkeypatch):
    serve(monkeypatch, OSError('no route'))
    with pytest.raises(ValueError, match='Cannot read the deployed'):
        real_gate(profile(tmp_path))
    with pytest.raises(ValueError, match='pm_ssh_host and pm_ssh_key'):
        real_gate(profile(tmp_path, pm_ssh_host=None))


def test_gate_covers_every_pm_mode_that_flashes_provisions_or_accepts():
    pm = {'require_pm': True}
    for mode in ('preflight', 'check', 'flash', 'transition', 'provision-pm', 'postflash'):
        assert gate.required(pm, mode)
    for mode in ('status', 'prepare', 'rejoin', 'metadata', 'restore-state', 'snapshot-state'):
        assert not gate.required(pm, mode)
    assert not gate.required({'require_pm': False, 'non_pm': True}, 'flash')


def test_campaign_refuses_before_dispatch_when_gate_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, 'load_profile', lambda path: dict(
        require_pm=True, postflash_role='EndDevice', preflash_role='EndDevice', workdir=str(tmp_path)))
    def refuse(profile): raise ValueError('Deployed Zigbee2MQTT converter does not recognise x')
    monkeypatch.setattr(gate, 'require_deployed_converter', refuse)
    monkeypatch.setattr(campaign.subprocess, 'call', lambda *a, **k: pytest.fail('dispatched'))
    monkeypatch.setattr(sys, 'argv', ['campaign', '--profile', 'p.json', '--mode', 'check'])
    with pytest.raises(SystemExit) as stop:
        campaign.main()
    assert stop.value.code == 4


def test_postflash_uses_the_gate_copy_unless_profile_pins_one(tmp_path):
    work = tmp_path / 'work'; work.mkdir()
    base = profile(tmp_path)
    assert gate.converter_copy(base) is None
    (work / gate.COPY_NAME).write_text(FAMILY, encoding='utf8')
    assert gate.converter_copy(base) == str(work / gate.COPY_NAME)
    assert gate.converter_copy(dict(base, deployed_converter='/pinned.js')) == '/pinned.js'
    assert gate.converter_copy(dict(base, require_pm=False)) is None
