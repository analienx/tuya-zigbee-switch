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
    monkeypatch.setattr(launcher, 'confirm_stopped', lambda *_: None)
    before = bytes((i * 17) % 255 for i in range(launcher.FLASH_BYTES))
    expected = image + b'\xff' * (launcher.ERASE_END - len(image)) + before[launcher.ERASE_END:]
    events = []
    def read(port, output, **kwargs):
        assert kwargs['reads'] == 1
        events.append('backup' if kwargs['halt'] else 'readback')
        data = before if kwargs['halt'] else expected
        output.write_bytes(data)
        tool.atomic_json(output.with_name(output.name + '.json'), {
            'status': 'READ_SINGLE', 'port': port, 'reads': [{'sha256': launcher.digest(data)}],
            'haltChecks': [{'cpuControls': [5, 5], 'divider': 54}],
        })
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


def test_verify_only_recovers_failed_read_without_write_or_halt(bench, monkeypatch):
    events, *_ = bench
    original = launcher.swire._read_flash
    def fail_after_write(port, output, **kwargs):
        if not kwargs['halt']:
            raise RuntimeError('first readback failed')
        return original(port, output, **kwargs)
    monkeypatch.setattr(launcher.swire, '_read_flash', fail_after_write)
    with pytest.raises(RuntimeError, match='verification read failed'):
        launcher.run('COM3', 300)
    assert events == ['backup', 'write']
    monkeypatch.setattr(launcher.swire, '_read_flash', original)
    monkeypatch.setattr(launcher, 'writer', lambda *_: pytest.fail('verification must never write'))
    launcher.verify_only('COM3')
    assert events == ['backup', 'write', 'readback']
    state = launcher.swire.read_json(next(launcher.RUNS.glob('*/summary.json')))
    assert state['phase'] == 'VERIFIED' and state['verificationAttempts'][-1]['status'] == 'VERIFIED'


def test_verification_reuses_exact_divider_without_reset_flags(tmp_path, monkeypatch):
    import subprocess
    commands = []
    monkeypatch.setattr(launcher, 'confirm_stopped', lambda port, divider: commands.append((port, divider)))
    def read(port, output, **kwargs):
        assert kwargs['halt'] is False and kwargs['reads'] == 1
        kwargs['runner']([sys.executable, 'reader.py', '-p', port, 'rf', '0', '0x80000', str(output)])
    def child(command, **kwargs):
        commands.append(command)
        kwargs['log_path'].write_text('SWire speed for CLK 25.0 MHz... ok.\n')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(launcher.swire, '_read_flash', read)
    monkeypatch.setattr(launcher.swire, 'run_reader_with_progress', child)
    launcher.readback('COM3', tmp_path / 'after.bin', tmp_path / 'reader.py',
                      {'haltChecks': [{'cpuControls': [5, 5], 'divider': 54}]}, lambda _: None)
    assert commands[0] == ('COM3', 54)
    assert commands[1][2:4] == ['-c', '25']
    assert not {'-t', '-r', 'wf', 'we', 'es', 'ea'} & set(commands[1])


def test_existing_recovery_ownership_blocks_sensor(bench):
    events, *_ = bench
    launcher.swire.atomic_json(launcher.swire.authority_path('COM3'), {'phase': 'HELD'})
    with pytest.raises(RuntimeError, match='existing recovery'):
        launcher.run('COM3', 300)
    assert events == []


@pytest.mark.parametrize('exit_code', [0, 1])
def test_real_programmer_child_gets_only_bounded_write_command(tmp_path, monkeypatch, exit_code):
    import json
    reader = tmp_path / 'reader.py'
    reader.write_text('import sys,json,pathlib\n'
                      'pathlib.Path(__file__).with_suffix(".args").write_text(json.dumps(sys.argv[1:]))\n'
                      f'print("Write to 0x000100...", flush=True)\nsys.exit({exit_code})\n')
    monkeypatch.setattr(launcher.swire, 'READER_SHA256', launcher.digest(reader.read_bytes()))
    image = tmp_path / 'image.bin'
    image.write_bytes(b'test')
    if exit_code:
        with pytest.raises(RuntimeError, match='Programmer exited 1'):
            launcher.writer(reader, image, 'COM3', tmp_path, lambda _: None)
    else:
        launcher.writer(reader, image, 'COM3', tmp_path, lambda _: None)
    arguments = json.loads(reader.with_suffix('.args').read_text())
    assert arguments == ['-p', 'COM3', '-b', '921600', 'wf', '0x0', str(image)]
    assert (tmp_path / 'write.log').is_file()


def test_stalled_programmer_child_is_stopped_before_return(tmp_path, monkeypatch):
    import subprocess
    reader = tmp_path / 'reader.py'
    reader.write_text('import time\ntime.sleep(60)\n')
    monkeypatch.setattr(launcher.swire, 'READER_SHA256', launcher.digest(reader.read_bytes()))
    children = []
    original = subprocess.Popen
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(launcher.subprocess, 'Popen', launch)
    ticks = iter(range(0, 10000, 121))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='stopped making progress'):
        launcher.writer(reader, tmp_path / 'image.bin', 'COM3', tmp_path, lambda _: None)
    assert len(children) == 1 and children[0].poll() is not None
