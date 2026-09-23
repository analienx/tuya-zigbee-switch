"""CLI and HTTP safety tests run only with offline mocked components."""
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_ota_workflow as op


def private_profile(tmp_path):
    server = tmp_path / 'private' / 'serve'
    server.mkdir(parents=True)
    image = server / 'forward.ota'
    image.write_bytes(b'checksum-pinned-OTA')
    payload = image.read_bytes()
    index = server / 'index.json'
    base = 'http://127.0.0.1:8787/'
    index.write_text(json.dumps([{'url': base + image.name,
                                  'sha512': hashlib.sha512(payload).hexdigest()}]))
    return dict(device='BedroomSocketCabinetRight', ieee='0xa4c13824a7005afb',
                non_pm=True, workdir=str(tmp_path / 'private' / 'attempt3'),
                image=str(image), sha256=hashlib.sha256(payload).hexdigest(),
                index_output=str(index), url=base + image.name,
                index_url=base + index.name)


def test_check_served_rejects_http_image_mismatch(tmp_path, monkeypatch):
    profile = private_profile(tmp_path)
    def wrong_image(url, timeout):
        return io.BytesIO(b'not-the-pinned-firmware')
    monkeypatch.setattr(op.urllib.request, 'urlopen', wrong_image)
    with pytest.raises(ValueError, match='HTTP-served firmware'):
        op.check_served(profile)


def test_check_served_accepts_only_exact_pinned_image_and_index(tmp_path, monkeypatch):
    profile = private_profile(tmp_path)
    def correct(url, timeout):
        path = profile['image'] if url == profile['url'] else profile['index_output']
        return io.BytesIO(Path(path).read_bytes())
    monkeypatch.setattr(op.urllib.request, 'urlopen', correct)
    assert op.check_served(profile) is True
    index = Path(profile['index_output'])
    index.write_text(json.dumps([{'url': 'http://127.0.0.1:8787/wrong.ota',
                                  'sha512': hashlib.sha512(Path(profile['image']).read_bytes()).hexdigest()}]))
    with pytest.raises(ValueError, match='OTA index URL'):
        op.check_served(profile)


def test_flash_cli_requires_exact_ieee_and_new_physical_risk_flags(tmp_path, monkeypatch):
    profile = private_profile(tmp_path)
    commands = []
    monkeypatch.setattr(op, 'load_profile', lambda _path: profile)
    monkeypatch.setattr(op, 'inspect_locks', lambda *args, **kwargs: [])
    monkeypatch.setattr(op, 'ready', lambda *args: commands.append('qualified'))
    monkeypatch.setattr(op, 'campaign', lambda *args, **kwargs: commands.append((args, kwargs)))
    base = ['operator', '--profile', str(tmp_path / 'private' / 'profile.json'),
            '--campaign-root', str(tmp_path / 'private'), '--mode', 'flash']
    monkeypatch.setattr(sys, 'argv', base + ['--confirm-ieee', profile['ieee']])
    with pytest.raises(SystemExit):
        op.main()
    assert commands == []
    monkeypatch.setattr(sys, 'argv', base + ['--confirm-ieee', '0x0000000000000000',
                                          '--confirm-load-unplugged', '--accept-nonrecoverable-ota-risk'])
    with pytest.raises(SystemExit):
        op.main()
    assert commands == []
    monkeypatch.setattr(sys, 'argv', base + ['--confirm-ieee', profile['ieee'],
                                          '--confirm-load-unplugged', '--accept-nonrecoverable-ota-risk'])
    op.main()
    assert commands[0] == 'qualified'
    assert len(commands) == 2
    assert commands[1][0][1] == 'flash'
    assert commands[1][1]['unloaded'] is True
    assert commands[1][1]['accept_risk'] is True


def test_readonly_audit_reports_locks_without_preparing_or_flashing(tmp_path, monkeypatch):
    profile = private_profile(tmp_path)
    seen = []
    monkeypatch.setattr(op, 'load_profile', lambda _path: profile)
    monkeypatch.setattr(op, 'inspect_locks', lambda *args, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(op, 'campaign', lambda *args, **kwargs: pytest.fail('Audit attempted campaign'))
    monkeypatch.setattr(op, 'ready', lambda *args: pytest.fail('Audit attempted preparation'))
    monkeypatch.setattr(sys, 'argv', ['operator', '--profile', str(tmp_path / 'private' / 'profile.json'),
                                    '--campaign-root', str(tmp_path / 'private'), '--mode', 'audit'])
    op.main()
    assert seen == [{'audit': True}]
