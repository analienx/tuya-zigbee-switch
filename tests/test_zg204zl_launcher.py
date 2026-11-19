"""No hardware access: exercise the launcher's operation and evidence gates."""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from serial.tools import list_ports

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import flash_zg204zl as launcher


@pytest.fixture
def bench(tmp_path, monkeypatch):
    tool = launcher.swire
    monkeypatch.setattr(tool, 'STATE_ROOT', tmp_path / 'state')
    monkeypatch.setattr(launcher, 'RUNS', tmp_path / 'runs')
    monkeypatch.setattr(launcher, 'IMAGE', tmp_path / 'firmware.bin')
    image = bytes(i % 251 for i in range(launcher.IMAGE_BYTES))
    launcher.IMAGE.write_bytes(image)
    monkeypatch.setattr(launcher, 'IMAGE_SHA', hashlib.sha256(image).hexdigest())
    monkeypatch.setattr(list_ports, 'comports', lambda: [SimpleNamespace(device='COM3')])
    monkeypatch.setattr(tool, 'ensure_reader', lambda **_: {'reader': str(tmp_path / 'reader.py')})
    before = bytes((i * 17) % 255 for i in range(launcher.FLASH_BYTES))
    expected = image + b'\xff' * (launcher.ERASE_END - len(image)) + before[launcher.ERASE_END:]
    events = []
    def read(port, output, **kwargs):
        assert kwargs['reads'] == 1
        events.append('backup' if kwargs['halt'] else 'readback')
        output.write_bytes(before if kwargs['halt'] else expected)
    def write(reader, snapshot, port, folder, report):
        events.append('write')
        state = tool.read_json(folder / 'summary.json')
        assert state['phase'] == 'WRITING' and state['writeAttempted']
        assert snapshot.read_bytes() == image
    monkeypatch.setattr(tool, '_read_flash', read)
    monkeypatch.setattr(launcher, 'writer', write)
    return events, before, expected, image


def test_one_backup_and_one_readback_with_full_preservation(bench):
    events, *_ = bench
    launcher.run('COM3', 300)
    assert events == ['backup', 'write', 'readback']
    state = launcher.swire.read_json(next(launcher.RUNS.glob('*/summary.json')))
    assert state['phase'] == 'VERIFIED' and state['preservationVerified']
    assert state['bootVerified'] is False


@pytest.mark.parametrize('offset', [8, launcher.IMAGE_BYTES, launcher.ERASE_END, 0x7FFFF])
def test_compare_rejects_each_changed_region(bench, offset):
    _, before, expected, image = bench
    changed = bytearray(expected)
    changed[offset] ^= 1
    with pytest.raises(RuntimeError, match='mismatch'):
        launcher.verify(before, bytes(changed), image)


def test_failed_backup_never_writes(bench, monkeypatch):
    events, *_ = bench
    def fail(*args, **kwargs):
        raise RuntimeError('backup fault')
    monkeypatch.setattr(launcher.swire, '_read_flash', fail)
    with pytest.raises(RuntimeError, match='backup fault'):
        launcher.run('COM3', 300)
    assert events == []
    state = launcher.swire.read_json(next(launcher.RUNS.glob('*/summary.json')))
    assert not state['writeAttempted']


def test_uncertain_write_blocks_another_attempt_even_with_new_output(bench, monkeypatch):
    events, *_ = bench
    def fail(*args):
        events.append('write-failed')
        raise RuntimeError('programmer fault')
    monkeypatch.setattr(launcher, 'writer', fail)
    with pytest.raises(RuntimeError, match='programmer fault'):
        launcher.run('COM3', 300)
    assert events == ['backup', 'write-failed']
    with pytest.raises(RuntimeError, match='Previous sensor write'):
        launcher.run('COM3', 300)
    assert events == ['backup', 'write-failed']


def test_changed_firmware_never_reads_or_writes(bench):
    events, *_ = bench
    launcher.IMAGE.write_bytes(b'wrong image')
    with pytest.raises(RuntimeError, match='hash mismatch'):
        launcher.run('COM3', 300)
    assert events == []


def test_failed_comparison_retains_stop_and_evidence(bench, monkeypatch):
    events, before, _, _ = bench
    original = launcher.swire._read_flash
    def bad(port, output, **kwargs):
        original(port, output, **kwargs)
        if not kwargs['halt']:
            output.write_bytes(before)
    monkeypatch.setattr(launcher.swire, '_read_flash', bad)
    with pytest.raises(RuntimeError, match='mismatch'):
        launcher.run('COM3', 300)
    state = launcher.swire.read_json(next(launcher.RUNS.glob('*/summary.json')))
    assert state['phase'] == 'FAILED' and state['writeAttempted']
    assert Path(state['folder'], 'before.bin').exists()
    assert Path(state['folder'], 'after.bin').exists()


def test_existing_recovery_ownership_blocks_sensor(bench):
    events, *_ = bench
    launcher.swire.atomic_json(launcher.swire.authority_path('COM3'), {'phase': 'HELD'})
    with pytest.raises(RuntimeError, match='existing recovery'):
        launcher.run('COM3', 300)
    assert events == []
