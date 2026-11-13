"""Offline tests for the TX-only SWire recovery tool. No serial port is opened.

Every UART byte the writer sends is decoded back into SWire register writes
and applied to a model of the TLSR8258 SPI flash controller, so the tests
check what would land in flash, not just which calls were made.
"""
import datetime
import hashlib
import io
import json
import random
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
import telink_swire_recovery as tool  # noqa: E402
from telink_swire_recovery import RecoveryError  # noqa: E402

FLASH_SIZE = 0x80000


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, 'STATE_ROOT', tmp_path / 'private-state')
    for name in ('ERASE_WAIT', 'PAGE_WAIT', 'STATUS_WAIT', 'STOP_CPU_SECONDS'):
        monkeypatch.setattr(tool, name, 0.0)


def make_image(size=0x3000, *, version=0x12053024, manufacturer=0x1141, image_type=43556, seed=1):
    rng = random.Random(seed)
    body = bytearray(rng.getrandbits(8) for _ in range(size - 4))
    struct.pack_into('<I', body, 0x02, version)
    body[6:8] = b'\x5d\x02'
    body[8:12] = b'KNLT'
    struct.pack_into('<HH', body, 0x12, manufacturer, image_type)
    struct.pack_into('<I', body, 0x18, size)
    for offset in tool.OTHER_START_MARKERS:
        if offset + 4 <= len(body):            # a slice past the end would grow the image
            body[offset:offset + 4] = b'\0\0\0\0'
    if size > 0x1200:
        body[0x1100:0x1200] = b'\xff' * 0x100     # an all-0xFF page is skipped
    return bytes(body) + struct.pack('<I', tool.telink_crc(bytes(body)))


def make_ota(image, *, version=None, manufacturer=None, image_type=None):
    version = u32(image, 2) if version is None else version
    manufacturer = struct.unpack_from('<H', image, 0x12)[0] if manufacturer is None else manufacturer
    image_type = struct.unpack_from('<H', image, 0x14)[0] if image_type is None else image_type
    header = struct.pack('<IHHHHHIH32sI', tool.OTA_MAGIC, 0x100, 56, 0, manufacturer, image_type,
                         version, 2, b'test'.ljust(32, b'\0'), 56 + 6 + len(image))
    return header + struct.pack('<HI', 0, len(image)) + image


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def decode(stream):
    """Turn pvvx TX-only UART bytes back into (address, data) SWire writes."""
    assert len(stream) % 10 == 0
    frames, current = [], None
    for start in range(0, len(stream), 10):
        chunk = stream[start:start + 10]
        assert chunk[9] == 0xFE and set(chunk) <= {0x80, 0xFE}
        value = sum(1 << (7 - bit) for bit in range(8) if chunk[1 + bit] == 0x80)
        if current is None:
            assert chunk[0] == 0x80 and value == 0x5A
            current = []
        elif chunk[0] == 0x80:
            assert value == 0xFF and len(current) >= 5 and current[3] == 0
            frames.append(((current[0] << 16) | (current[1] << 8) | current[2], bytes(current[4:])))
            current = None
        else:
            current.append(value)
    assert current is None
    return frames


class Chip:
    """TLSR8258 SWire slave, SPI master registers and a NOR flash."""

    def __init__(self, flash):
        self.flash = bytearray(flash)
        self.stopped = False
        self.restarts = 0
        self.fifo = False
        self.selected = False
        self.spi = []
        self.write_enabled = False
        self.status = None
        self.divider = None
        self.erases = []
        self.ignored = []                                  # commands a real flash would drop

    def write(self, address, data):
        for index, value in enumerate(data):
            self.register(address if self.fifo and address == 0x0C else address + index, value)

    def register(self, address, value):
        if address == 0x6F:
            assert value == 0x20
            self.restarts += 1
            self.stopped = False
        elif address == 0x602:
            assert value == 0x05
            self.stopped = True
        elif address == 0xB2:
            self.divider = value
        else:
            assert self.stopped, f'SPI register {address:#x} written while the CPU runs'
            if address == 0xB3:
                self.fifo = value == 0x80
            elif address == 0x0D:
                if value == 0:
                    self.selected, self.spi = True, []
                else:
                    self.deselect()
            elif address == 0x0C:
                assert self.selected
                self.spi.append(value)
            else:
                raise AssertionError(f'unexpected register {address:#x}')

    def deselect(self):
        assert self.selected
        self.selected = False
        if not self.spi:
            return                                      # the command frame was lost
        command, args = self.spi[0], self.spi[1:]
        if command == 0x06:
            self.write_enabled = True
            return
        if command == 0xAB:
            return
        if command not in (0x01, 0x20, 0x02) or not self.write_enabled:
            self.ignored.append(command)                   # unknown, or no write enable
            return
        self.write_enabled = False
        if command == 0x01:
            self.status = args
        elif command == 0x20:
            assert len(args) == 3
            address = (args[0] << 16) | (args[1] << 8) | args[2]
            assert address % 0x1000 == 0
            self.flash[address:address + 0x1000] = b'\xff' * 0x1000
            self.erases.append(address)
        elif command == 0x02:
            address = (args[0] << 16) | (args[1] << 8) | args[2]
            data = args[3:]
            assert data and address // 256 == (address + len(data) - 1) // 256
            for index, value in enumerate(data):
                self.flash[address + index] &= value


class ChipPort:
    """Serial port whose bytes go straight into the chip model."""

    def __init__(self, chip, drop=lambda address, data, count: False):
        self.chip = chip
        self.drop = drop
        self.count = 0

    def write(self, buf):
        for address, data in decode(bytes(buf)):
            self.count += 1
            if not self.drop(address, data, self.count):
                self.chip.write(address, data)
        return len(buf)

    def flush(self):
        pass

    def read(self, *_):
        raise AssertionError('the TX-only writer must never read')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def bricked_flash(seed=7):
    """Enabled image in slot A, disabled one in slot B, live NVM, stale markers."""
    rng = random.Random(seed)
    flash = bytearray(rng.getrandbits(8) for _ in range(FLASH_SIZE))
    old = make_image(0x2E00, seed=21)
    flash[:len(old)] = old
    flash[0x40000 + 8] = 0x00
    flash[0x40009:0x4000C] = b'NLT'
    for offset in tool.OTHER_START_MARKERS:        # left over from an older layout
        flash[offset:offset + 4] = b'KNLT'
    return bytes(flash)


def clock():
    ticks = iter(range(10 ** 6))
    return lambda: next(ticks)


def writer_for(chip, **kwargs):
    return tool.TxOnlyWriter(ChipPort(chip, **kwargs), sleep=lambda _: None, clock=clock())


def assert_recovered(chip, before, image):
    end = tool.erase_end(len(image))
    assert end <= tool.SLOT_A_END
    assert bytes(chip.flash[:len(image)]) == image
    assert set(chip.flash[len(image):end]) <= {0xFF}
    assert chip.flash[end:] == before[end:], 'something outside slot A changed'
    for offset in tool.OTHER_START_MARKERS:
        assert chip.flash[offset:offset + 4] != b'KNLT', f'stale start marker left at {offset:#x}'


def test_frames_match_pvvx_v05_javascript():
    # Vectors computed from the original sws_wr_addr() JavaScript; shared
    # with analienx/gledopto tools/tests/test_glsd301p_stock_txonly.py.
    cases = [
        (0x00B3, b'\x80', 'c9f62130d26b1e7d0bc21b1bcf93c62e12970706b2ff095e976bbebbbb1b8809'),
        (0x000C, b'\x06\x01', '8df7fb7003ed35312fac58b14b8c285897c91144d9085fdfcfab60da0afb313d'),
        (0x0008, b'\x4b', 'd4cf4c583339908a0c0b6e864647c57b7078986a8b963342ea18a111267520a6'),
    ]
    for address, value, expected in cases:
        frame = tool.swire_frame(address, value)
        assert hashlib.sha256(frame).hexdigest() == expected
        assert decode(frame) == [(address, value)]


def test_stage_then_activate_rewrites_only_slot_a():
    before = bricked_flash()
    image = make_image(0x5432)
    chip = Chip(before)
    writer = writer_for(chip)
    writer.stage(image)
    assert chip.flash[8:12] == b'\xffNLT', 'stage must leave the start marker off'
    # Two erasing passes over every sector up to 0x21000, the small-OTA area included.
    assert chip.erases == list(range(0, 0x21000, 0x1000)) * 2
    assert chip.divider == tool.SWS_DIVIDER and chip.status == [0]
    writer.activate()
    assert_recovered(chip, before, image)
    assert not chip.stopped and chip.restarts == 2  # do not reset again between stage and activation
    assert chip.ignored == []


def test_full_size_image_stays_below_nvm():
    before = bricked_flash()
    image = make_image(0x32264, seed=22)   # the r22 Router size
    chip = Chip(before)
    writer = writer_for(chip)
    writer.stage(image)
    writer.activate()
    assert_recovered(chip, before, image)
    assert max(chip.erases) == 0x32000
    assert chip.ignored == []


def drop_page(occurrence):
    """Lose the nth program of page 0x2000 on the wire."""
    seen = []

    def drop(address, data, count):
        if address == 0x0C and data[:4] == b'\x02\x00\x20\x00':
            seen.append(count)
            return len(seen) == occurrence
        return False
    return drop


def drop_erase(occurrence):
    """Lose the opcode of the nth sector erase (sector 0 is the first of each pass)."""
    state = {'last': None, 'seen': 0}

    def drop(address, data, count):
        last, state['last'] = state['last'], (address, data)
        if (address, data) == (0x0C, b'\x20') and last == (0x0D, b'\x00'):
            state['seen'] += 1
            return state['seen'] == occurrence
        return False
    return drop


SECTORS = tool.erase_end(0x5432) // 0x1000


@pytest.mark.parametrize('loss, passes, recovered', [
    (lambda: drop_page(1), 1, False),
    (lambda: drop_page(1), 3, True),
    (lambda: drop_page(2), 3, True),          # lost after its pass-2 erase landed
    (lambda: drop_erase(1), 2, False),        # old marker survives: must not boot
    (lambda: drop_erase(1), 3, True),
    (lambda: drop_erase(SECTORS + 1), 3, True),
], ids=['page-1pass', 'page', 'page-pass2', 'erase0-2pass', 'erase0', 'erase0-pass2'])
def test_three_passes_survive_one_lost_frame(loss, passes, recovered):
    image = make_image(0x5432)
    before = bricked_flash()
    chip = Chip(before)
    writer = writer_for(chip, drop=loss())
    writer.stage(image, passes)
    if not recovered and passes == 2:
        assert chip.flash[8:12] == b'KNLT', 'the case the third pass exists for'
    writer.activate()
    assert (bytes(chip.flash[:len(image)]) == image) is recovered
    assert chip.flash[0x21000:] == before[0x21000:]


def test_writer_refuses_everything_outside_slot_a():
    writer = writer_for(Chip(bricked_flash()))
    for address in (-0x1000, 0x34000, 0x40000, 0x76000, 0x123):
        with pytest.raises(RecoveryError):
            writer.erase(address)
    with pytest.raises(RecoveryError):
        writer.program(0x33F00, b'\0' * 0x101)
    with pytest.raises(RecoveryError):
        writer.program(0x34000, b'\0')
    with pytest.raises(RecoveryError):
        writer.program(0xFF, b'\0\0')
    with pytest.raises(RecoveryError):
        writer.program(0, b'\0' * 12)              # would switch the marker on during stage
    with pytest.raises(RecoveryError):
        writer.program(0x40008, b'K', marker=True)
    for passes in (0, 4):
        with pytest.raises(RecoveryError):
            writer.stage(make_image(), passes=passes)


def test_ota_extraction_and_rejections():
    image = make_image(0x5432)
    extracted, info = tool.image_from_ota(make_ota(image))
    assert extracted == image
    assert info['fileVersion'] == 0x12053024 and info['imageType'] == 43556

    with pytest.raises(RecoveryError, match='wrapper'):
        tool.image_from_ota(make_ota(image, version=0xFFFFFFFF))
    with pytest.raises(RecoveryError, match='wrapper'):
        tool.image_from_ota(make_ota(image, image_type=54179))
    broken = bytearray(image)
    broken[0x2000] ^= 1
    with pytest.raises(RecoveryError, match='CRC'):
        tool.check_image(bytes(broken))
    raw = bytearray(image)
    raw[6:8] = b'\0\0'                               # forward.bin before finalization
    with pytest.raises(RecoveryError, match='forward.bin'):
        tool.check_image(bytes(raw))
    big = bytearray(make_image(0x21000, seed=3)[:-4])
    big[0x20008:0x2000C] = b'KNLT'
    with pytest.raises(RecoveryError, match='second start marker'):
        tool.check_image(bytes(big) + struct.pack('<I', tool.telink_crc(bytes(big))))
    with pytest.raises(RecoveryError, match='slot A holds'):
        tool.check_image(b'\0' * 0x34000)
    ota = bytearray(make_ota(image))
    struct.pack_into('<I', ota, 52, len(ota) + 1)
    with pytest.raises(RecoveryError, match='total size'):
        tool.image_from_ota(bytes(ota))


CLIENT_SHA = 'c' * 64


def ci_artifact(folder, image, *, dirty=False, commit='a' * 40):
    """forward.ota, manifest.json and ROLE_MATRIX.json shaped like the PM matrix artifact."""
    ota = make_ota(image)
    digest = hashlib.sha256(ota).hexdigest()
    (folder / 'forward.ota').write_bytes(ota)
    (folder / 'manifest.json').write_text(json.dumps({
        'artifacts': {'forward.ota': {'bytes': len(ota), 'sha256': digest}},
        'sourceDirty': dirty, 'sourceCommit': commit, 'board': 'OUTLET_BSEED_PM_TS011F',
        'swBuildId': '1.2.5-bseedr22', 'buildDate': '20261106',
        'otaHeader': {'manufacturerCode': 0x1141, 'imageType': 43556, 'fileVersion': 0x12053024},
    }))

    def evidence(own, other):
        return {'tc32Boot': {'otaSha256': own, 'slots': [0, 0x40000], 'appLoopEntriesPerSlot': 3,
                             'restartsPerSlot': 1},
                'tc32Activation': {'activatorOtaSha256': own, 'stagedOtaSha256': other,
                                   'staging': ['complete', 'partial', 'corrupt-crc'],
                                   'slotDirections': 2}}
    write_matrix(folder, {'sourceCommit': commit, 'compiledBothRoles': True, 'reproducedBothRoles': True,
                          'artifacts': [
                              {'role': 'Router', 'build': '1.2.5-bseedr22', 'sha256': digest,
                               **evidence(digest, CLIENT_SHA)},
                              {'role': 'EndDevice', 'build': '1.2.5-bseedcli25', 'sha256': CLIENT_SHA,
                               **evidence(CLIENT_SHA, digest)}]})
    return digest


def write_matrix(folder, data):
    (folder / 'ROLE_MATRIX.json').write_text(json.dumps(data))


def read_matrix(folder):
    return json.loads((folder / 'ROLE_MATRIX.json').read_text())


def prepare_from(folder, out, **kwargs):
    return tool.prepare(folder / 'forward.ota', out, manifest=folder / 'manifest.json',
                        role_matrix=folder / 'ROLE_MATRIX.json', **kwargs)


def test_prepare_bundle_from_a_ci_artifact(tmp_path):
    image = make_image(0x5432)
    digest = ci_artifact(tmp_path, image)
    plan = prepare_from(tmp_path, tmp_path / 'kit', expect_ota_sha256=digest,
                        expect_build='1.2.5-bseedr22')
    assert plan['image']['sha256'] == hashlib.sha256(image).hexdigest()
    assert plan['source']['roleMatrix']['role'] == 'Router'
    assert plan['slotA'] == {'eraseFrom': '0x00000', 'eraseTo': '0x21000', 'sectors': 33,
                             'markerOffset': '0x08'}
    assert plan['neverWritten'] == '0x21000-0x7ffff'
    assert plan['transport']['passes'] == 3
    loaded, data = tool.load_bundle(tmp_path / 'kit')
    assert data == image and loaded == plan
    assert 'role Router' in tool.describe(plan)


MATRIX_FAULTS = {
    'commit': lambda m: m.update(sourceCommit='b' * 40),
    'not_reproduced': lambda m: m.update(reproducedBothRoles=False),
    'boot_other_image': lambda m: m['artifacts'][0]['tc32Boot'].update(otaSha256='0' * 64),
    'no_restart': lambda m: m['artifacts'][0]['tc32Boot'].update(restartsPerSlot=0),
    'one_slot': lambda m: m['artifacts'][0]['tc32Boot'].update(slots=[0]),
    'activator_other_image': lambda m: m['artifacts'][0]['tc32Activation'].update(activatorOtaSha256='0' * 64),
    'staged_unlisted': lambda m: m['artifacts'][0]['tc32Activation'].update(stagedOtaSha256='0' * 64),
    'no_corrupt_case': lambda m: m['artifacts'][0]['tc32Activation']['staging'].remove('corrupt-crc'),
    'one_direction': lambda m: m['artifacts'][0]['tc32Activation'].update(slotDirections=1),
    'listed_twice': lambda m: m['artifacts'].append(dict(m['artifacts'][0])),
}


@pytest.mark.parametrize('fault', ['dirty', 'build', 'sha', 'not_empty', *MATRIX_FAULTS])
def test_prepare_rejects_inconsistent_evidence(tmp_path, fault):
    image = make_image(0x5432)
    ci_artifact(tmp_path, image, dirty=fault == 'dirty')
    if fault in MATRIX_FAULTS:
        matrix = read_matrix(tmp_path)
        MATRIX_FAULTS[fault](matrix)
        write_matrix(tmp_path, matrix)
    if fault == 'not_empty':
        (tmp_path / 'kit').mkdir()
        (tmp_path / 'kit' / 'old.txt').write_text('x')
    kwargs = {'expect_build': '1.2.5-bseedr21'} if fault == 'build' else {}
    if fault == 'sha':
        kwargs = {'expect_ota_sha256': '0' * 64}
    with pytest.raises(RecoveryError):
        prepare_from(tmp_path, tmp_path / 'kit', **kwargs)
    assert fault == 'not_empty' or not (tmp_path / 'kit').exists()


def test_manifest_for_another_file_is_rejected(tmp_path):
    ci_artifact(tmp_path, make_image(0x5432))
    (tmp_path / 'forward.ota').write_bytes(make_ota(make_image(0x5432, seed=9)))
    with pytest.raises(RecoveryError, match='manifest does not describe'):
        prepare_from(tmp_path, tmp_path / 'kit')


def test_tampered_bundle_is_refused(tmp_path):
    ci_artifact(tmp_path, make_image(0x5432))
    prepare_from(tmp_path, tmp_path / 'kit')
    image = bytearray((tmp_path / 'kit' / 'image.bin').read_bytes())
    image[0x3000] ^= 0xFF
    (tmp_path / 'kit' / 'image.bin').write_bytes(bytes(image))
    with pytest.raises(RecoveryError):
        tool.load_bundle(tmp_path / 'kit')


def test_run_write_end_to_end_with_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(tool.sys, 'stdin', io.StringIO())   # not a terminal
    image = make_image(0x5432)
    ci_artifact(tmp_path, image)
    kit = tmp_path / 'kit'
    plan = prepare_from(tmp_path, kit)
    token = plan['image']['sha256'][:8]
    before = bricked_flash()
    chip = Chip(before)
    opener = lambda port: ChipPort(chip)
    lines = []
    activate = f'ACTIVATE-{token}'
    with pytest.raises(RecoveryError, match='needs a finished stage'):
        tool.run_write('activate', kit, 'COM5', activate, opener=opener, out=lines.append)
    with pytest.raises(RecoveryError, match='--confirm must be'):
        tool.run_write('stage', kit, 'COM5', 'STAGE-00000000', opener=opener, out=lines.append)
    with pytest.raises(RecoveryError, match='not run interactively'):
        tool.run_write('stage', kit, 'COM5', None, opener=opener, out=lines.append)
    assert chip.flash == bytearray(before), 'refused runs must not send anything'
    tool.run_write('stage', kit, 'COM5', f'STAGE-{token}', opener=opener, out=lines.append)
    assert chip.flash[8] == 0xFF
    later = lambda: tool.utc_now() + datetime.timedelta(minutes=31)
    for port, now in (('COM6', tool.utc_now), ('COM5', later)):   # another port, or too late
        with pytest.raises(RecoveryError):
            tool.run_write('activate', kit, port, activate, opener=opener, out=lines.append, now=now)
    tool.run_write('activate', kit, 'COM5', activate, opener=opener, out=lines.append)
    assert_recovered(chip, before, image)
    log = tool.receipts(kit)
    assert [(entry['action'], entry['result']) for entry in log] == [
        ('stage', 'STARTED'), ('stage', 'SENT_UNVERIFIED'),
        ('activate', 'STARTED'), ('activate', 'SENT_UNVERIFIED')]
    assert log[1]['passes'] == 3 and log[1]['sectorsErased'] == 2 * SECTORS


class Unplugged(ChipPort):
    """The adapter disappears partway through."""

    def write(self, buf):
        if self.count > 400:
            raise KeyboardInterrupt
        return super().write(buf)


def test_interrupted_stage_is_recorded_and_blocks_activate(tmp_path):
    ci_artifact(tmp_path, make_image(0x5432))
    kit = tmp_path / 'kit'
    token = prepare_from(tmp_path, kit)['image']['sha256'][:8]
    chip = Chip(bricked_flash())
    with pytest.raises(RecoveryError, match='did not finish'):
        tool.run_write('stage', kit, 'COM5', f'STAGE-{token}', opener=lambda port: Unplugged(chip),
                       out=lambda _: None)
    assert [entry['result'] for entry in tool.receipts(kit)] == ['STARTED', 'INTERRUPTED: KeyboardInterrupt']
    with pytest.raises(RecoveryError, match='cannot activate'):
        tool.run_write('activate', kit, 'COM5', f'ACTIVATE-{token}', opener=lambda port: ChipPort(chip),
                       out=lambda _: None)


def test_flash_runs_both_phases_in_one_session(tmp_path):
    image = make_image(0x5432)
    ci_artifact(tmp_path, image)
    plan = prepare_from(tmp_path, tmp_path / 'kit')
    before = bricked_flash()
    chip = Chip(before)
    tool.run_write('flash', tmp_path / 'kit', 'COM5', f'FLASH-{plan["image"]["sha256"][:8]}',
                   opener=lambda port: ChipPort(chip), out=lambda _: None)
    assert_recovered(chip, before, image)
    assert [entry['action'] for entry in tool.receipts(tmp_path / 'kit')
            if entry['result'] == 'SENT_UNVERIFIED'] == ['stage', 'activate']


def test_estimate_is_minutes_not_hours():
    seconds = tool.estimate_seconds(make_image(0x32264, seed=22))
    assert 60 <= seconds <= 400


class Loopback:
    def __init__(self, lose=0):
        self.buffer = b''
        self.lose = lose

    def reset_input_buffer(self):
        self.buffer = b''

    def write(self, data):
        self.buffer += data[self.lose:]
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data


def test_selftest_detects_a_broken_loop():
    assert tool.selftest(Loopback(), seconds=0.1)[0]
    assert not tool.selftest(Loopback(lose=1), seconds=0.1)[0]


def test_cli_prepare_and_plan(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(tool.sys, 'stdin', io.StringIO())
    ci_artifact(tmp_path, make_image(0x5432))
    kit = tmp_path / 'kit'
    assert tool.main(['prepare', '--ota', str(tmp_path / 'forward.ota'), '--out', str(kit),
                      '--manifest', str(tmp_path / 'manifest.json'),
                      '--role-matrix', str(tmp_path / 'ROLE_MATRIX.json')]) == 0
    assert tool.main(['plan', '--bundle', str(kit)]) == 0
    assert tool.main(['flash', '--bundle', str(kit), '--port', 'COM5']) == 2
    assert tool.main(['flash', '--bundle', str(kit), '--port', 'not a port', '--confirm', 'x']) == 2
    assert 'BLOCKED' in capsys.readouterr().err


def test_ci_publishes_a_wired_kit_for_every_socket_role():
    for workflow in ('bseed-pm-matrix.yml', 'bseed-nonpm-client-canary.yml'):
        text = (ROOT / '.github' / 'workflows' / workflow).read_text(encoding='utf-8')
        assert 'helper_scripts/telink_swire_recovery.py prepare' in text
        assert '--role-matrix' in text and 'for role in router client' in text
        assert 'wired-kit-${{ github.event.pull_request.head.sha || github.sha }}' in text


@pytest.mark.parametrize('fault', [None, 'short', 'different', 'exit', 'timeout'])
def test_optional_reader_double_read_and_fail_closed(tmp_path, monkeypatch, fault):
    import subprocess
    reader = tmp_path / 'reader.py'
    reader.write_bytes(b'pinned source fixture')
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[1] == str(reader)
        assert command[command.index('rf') + 1: -1] == ['0x0', '0x20']
        assert not {'ea', 'es', 'wf', 'we', '-t', '-r'} & set(command)
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(command, 1800)
        data = b'a' * 32
        if fault == 'short':
            data = data[:-1]
        if fault == 'different' and len(calls) == 2:
            data = b'b' * 32
        Path(command[-1]).write_bytes(data)
        return subprocess.CompletedProcess(command, 1 if fault == 'exit' else 0)

    target = tmp_path / 'flash.bin'
    if fault:
        with pytest.raises((RecoveryError, subprocess.TimeoutExpired)):
            tool.read_flash('COM5', target, size=32, reader=reader, runner=run)
        assert json.loads(target.with_name('flash.bin.json').read_text())['status'] == 'READ_FAILED'
    else:
        result = tool.read_flash('COM5', target, size=32, reader=reader, runner=run)
        assert result['status'] == 'READ_MATCHED' and len(calls) == 2
        with pytest.raises(RecoveryError, match='already exists'):
            tool.read_flash('COM5', target, size=32, reader=reader, runner=run)


def test_reader_rejects_untrusted_source_ranges_and_repository_outputs(tmp_path):
    reader = tmp_path / 'untrusted.py'
    reader.write_text('untrusted')
    with pytest.raises(RecoveryError, match='pinned'):
        tool.read_flash('COM5', tmp_path / 'dump', reader=reader)
    with pytest.raises(RecoveryError, match='range'):
        tool.read_flash('COM5', tmp_path / 'dump', address=0x7ffff, size=2)
    (tmp_path / '.git').mkdir()
    with pytest.raises(RecoveryError, match='outside'):
        tool.read_flash('COM5', tmp_path / 'dump')


def test_verify_staged_image_tail_and_protected_data(tmp_path):
    image = make_image()
    ci_artifact(tmp_path, image)
    kit = tmp_path / 'kit'
    prepare_from(tmp_path, kit)
    before = bytes([0x55]) * tool.FLASH_BYTES
    after = bytearray(before)
    end = tool.erase_end(len(image))
    after[:end] = b'\xff' * end
    after[:len(image)] = image
    after[8] = 0xff
    baseline, dump = tmp_path / 'before', tmp_path / 'after'
    baseline.write_bytes(before)
    dump.write_bytes(after)
    assert tool.verify_dump(kit, dump, staged=True, baseline=baseline)['preservationVerified']
    with pytest.raises(RecoveryError, match='image'):
        tool.verify_dump(kit, dump)
    after[0x76000] ^= 1
    dump.write_bytes(after)
    with pytest.raises(RecoveryError, match='preserved'):
        tool.verify_dump(kit, dump, staged=True, baseline=baseline)


def test_device_profiles_block_cross_device_and_quarantined_images():
    info = tool.check_image(make_image(manufacturer=0x124f, image_type=0x1416))
    with pytest.raises(RecoveryError, match='manufacturer'):
        tool.check_device('bseed-ts011f', info, {})
    with pytest.raises(RecoveryError, match='quarantined'):
        tool.check_device('gledopto-gl-sd-301p', info, {'buildManifest': {'DEPLOYABLE': '0'}})
    info['sha256'] = next(iter(tool.DEVICES['gledopto-gl-sd-301p']['images']))
    assert tool.check_device('gledopto-gl-sd-301p', info, {})


@pytest.mark.parametrize('image_type', [43555, 43556, 65024, 65026])
def test_bseed_four_native_identities(image_type):
    info = tool.check_image(make_image(image_type=image_type))
    board = 'OUTLET_BSEED_PM_TS011F' if image_type in (43556, 65024) else 'OUTLET_BSEED_TS011F'
    assert tool.check_device('bseed-ts011f', info, {
        'manifest': {'board': board, 'swBuildId': 'fixture'},
        'roleMatrix': {'role': 'Router' if image_type in (43555, 43556) else 'EndDevice', 'build': 'fixture'}})


def test_inherited_cli_json_wiring_and_dry_run(tmp_path, capsys):
    ci_artifact(tmp_path, make_image())
    kit = tmp_path / 'kit'
    prepare_from(tmp_path, kit)
    assert tool.main(['wiring', '--device', 'gledopto-gl-sd-301p', '--json']) == 0
    assert 'gledopto-gl-sd-301p' in json.loads(capsys.readouterr().out)['devices']
    stream = tmp_path / 'stream.uart'
    assert tool.main(['flash', '--bundle', str(kit), '--dry-run', str(stream), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'DRY_RUN'
    assert result['streamSha256'] == tool.sha256(stream.read_bytes())
    assert not (kit / tool.BUNDLE_LOG).exists()


def full_backup(path, data, port='COM5'):
    path.write_bytes(data)
    path.with_name(path.name + '.second.bin').write_bytes(data)
    path.with_name(path.name + '.json').write_text(json.dumps({
        'status': 'READ_MATCHED', 'address': 0, 'bytes': FLASH_SIZE, 'port': port,
        'utc': tool.utc_now().isoformat(),
        'reads': [{'sha256': tool.sha256(data)}] * 2}))
    return path


def two_known_slots(active='B'):
    data = bytearray(bricked_flash())
    for offset in tool.OTHER_START_MARKERS:
        data[offset:offset + 4] = b'\xff' * 4
    images = {'r19': make_image(0x3120, seed=19, version=0x12053021),
              'r21': make_image(0x3220, seed=21, version=0x12053023)}
    for slot, base in tool.SLOTS.items():
        image = images['r21' if slot == active else 'r19']
        data[base:base + len(image)] = image
        if slot != active:
            data[base + 8] = 0
    return data, images


@pytest.mark.parametrize('slot', ['A', 'B'])
@pytest.mark.parametrize('hold', [False, True])
def test_full_slot_wire_model_in_both_directions(tmp_path, slot, hold):
    before, _ = two_known_slots(slot)
    image = make_image(0x5432)
    chip = Chip(before)
    writer = tool.TxOnlyWriter(ChipPort(chip), sleep=lambda _: None, clock=clock(),
                               slot=slot, full_slot=True, hold=hold)
    base = tool.SLOTS[slot]
    writer.stage(image)
    assert chip.flash[base + 8:base + 12] == b'\xffNLT'
    writer.activate()
    assert chip.flash[base:base + len(image)] == image
    assert chip.flash[base + len(image):base + 0x34000] == b'\xff' * (0x34000 - len(image))
    assert chip.flash[:base] == before[:base]
    assert chip.flash[base + 0x34000:] == before[base + 0x34000:]
    assert chip.erases == list(range(base, base + 0x34000, 0x1000)) * 2
    assert chip.stopped == hold
    assert chip.restarts == (1 if hold else 2)
    ci_artifact(tmp_path, image)
    prepare_from(tmp_path, tmp_path / 'kit')
    dump, baseline = tmp_path / 'after', tmp_path / 'before'
    dump.write_bytes(chip.flash)
    baseline.write_bytes(before)
    assert tool.verify_dump(tmp_path / 'kit', dump, slot=slot, full_slot=True,
                            baseline=baseline)['preservationVerified']
    # Mutations in the other slot, either NVM region and factory data must fail.
    for address in (tool.SLOTS['B' if slot == 'A' else 'A'] + 20, 0x34000, 0x76000, 0x7a000):
        corrupt = bytearray(chip.flash)
        corrupt[address] ^= 1
        dump.write_bytes(corrupt)
        with pytest.raises(RecoveryError, match='preserved'):
            tool.verify_dump(tmp_path / 'kit', dump, slot=slot, full_slot=True, baseline=baseline)


def test_slot_b_writer_cannot_touch_slot_a_or_nvm():
    writer = tool.TxOnlyWriter(ChipPort(Chip(bricked_flash())), slot='B')
    for address in (0, 0x33000, 0x34000, 0x74000, 0x76000, 0x7a000):
        with pytest.raises(RecoveryError):
            writer.erase(address)
        with pytest.raises(RecoveryError):
            writer.program(address, b'\xff')
    with pytest.raises(RecoveryError):
        writer.program(8, b'K', marker=True)
    with pytest.raises(RecoveryError):
        writer.program(0x40000, b'\x00' * 12)


@pytest.mark.parametrize('active', ['A', 'B'])
def test_identify_failed_slot_by_exact_reference_with_disabled_marker(tmp_path, active):
    before, images = two_known_slots(active)
    dump = full_backup(tmp_path / 'dump', before)
    refs = []
    for name, image in images.items():
        path = tmp_path / (name + '.ota')
        path.write_bytes(make_ota(image))
        refs.append(f'{name}={path}')
    result = tool.inspect_slots(dump, refs, replace='r21', expect_other='r19')
    assert result['replaceSlot'] == active and not result['bootVerified']
    assert all(s['validImage'] for s in result['slots'])
    assert {s['markerState'] for s in result['slots']} == {'enabled', 'disabled'}
    # Each of these invalidates the expected incident pattern.
    other = tool.SLOTS['B' if active == 'A' else 'A']
    for address, value in ((other + 8, 0x4b), (tool.SLOTS[active] + 8, 0xff),
                           (tool.SLOTS[active] + 0x100, before[tool.SLOTS[active] + 0x100] ^ 1)):
        corrupt = bytearray(before)
        corrupt[address] = value
        full_backup(dump, corrupt)
        with pytest.raises(RecoveryError, match='unique enabled'):
            tool.inspect_slots(dump, refs, replace='r21', expect_other='r19')


def test_slot_b_write_requires_baseline_and_slot_bound_receipt(tmp_path):
    image = make_image()
    ci_artifact(tmp_path, image)
    kit = tmp_path / 'kit'
    prepare_from(tmp_path, kit)
    before, _ = two_known_slots('B')
    dump = full_backup(tmp_path / 'baseline', before)
    chip = Chip(before)
    token = tool.sha256(image)[:8]
    options = dict(slot='B', full_slot=True, hold=True, opener=lambda p: ChipPort(chip), out=lambda _: None)
    with pytest.raises(RecoveryError, match='requires --baseline'):
        tool.run_write('stage', kit, 'COM5', f'STAGE-B-FULL-{token}', **options)
    # Wrong-layout confirmation cannot accidentally erase B.
    with pytest.raises(RecoveryError, match='--confirm must'):
        tool.run_write('stage', kit, 'COM5', f'STAGE-{token}', baseline=dump, **options)
    tool.run_write('stage', kit, 'COM5', f'STAGE-B-FULL-{token}', baseline=dump, **options)
    assert tool.fresh_stage(kit, tool.sha256(image), 'COM5', slot='B', full_slot=True,
                            baseline_sha=tool.sha256(before))
    assert not tool.fresh_stage(kit, tool.sha256(image), 'COM5', slot='B', full_slot=True)
    assert not tool.fresh_stage(kit, tool.sha256(image), 'COM5')
    staged = full_backup(tmp_path / 'staged', chip.flash)
    tool.verify_dump(kit, staged, staged=True, baseline=dump, slot='B', full_slot=True,
                     session=tool.default_session('COM5'))
    tool.run_write('activate', kit, 'COM5', f'ACTIVATE-B-FULL-{token}', baseline=dump, **options)
    assert chip.flash[0x40000:0x40000 + len(image)] == image
    assert chip.stopped
    assert chip.flash[:0x40000] == before[:0x40000]
    before[8] = 0x4b
    full_backup(dump, before)
    with pytest.raises(RecoveryError, match='slot A is not explicitly disabled'):
        tool.run_write('stage', kit, 'COM5', f'STAGE-B-FULL-{token}', baseline=dump, **options)


def test_backup_validation_rejects_changed_evidence(tmp_path):
    data, _ = two_known_slots()
    dump = full_backup(tmp_path / 'dump', data)
    with pytest.raises(RecoveryError, match='double read'):
        tool.matched_backup(dump, port='COM7')
    dump.with_name(dump.name + '.second.bin').write_bytes(bytes(FLASH_SIZE))
    with pytest.raises(RecoveryError, match='double read'):
        tool.inspect_slots(dump)


def agent_fixture(tmp_path, *, baseline=False):
    image = make_image()
    ci_artifact(tmp_path, image)
    kit = tmp_path / 'kit'
    plan = prepare_from(tmp_path, kit)
    before, _ = two_known_slots('B' if baseline else 'A')
    chip = Chip(before)
    session = tmp_path / 'private-session.json'
    options = dict(session=session, target_id='fixture-target', opener=lambda p: ChipPort(chip), out=lambda _: None)
    if baseline:
        options.update(slot='B', full_slot=True, baseline=full_backup(tmp_path / 'baseline', before))
    token = lambda action: tool.confirmation_token(action, plan['image']['sha256'],
                                                  options.get('slot', 'A'), options.get('full_slot', False))
    return kit, chip, session, options, token


def test_agent_plan_json_matches_selected_slot(tmp_path, capsys):
    kit, _, _, _, _ = agent_fixture(tmp_path)
    assert tool.main(['--json', 'plan', '--bundle', str(kit), '--slot', 'B', '--full-slot']) == 0
    plan = json.loads(capsys.readouterr().out)['plan']
    assert 'slotA' not in plan and 'neverWritten' not in plan
    assert plan['target']['eraseStart'] == 0x40000
    assert plan['target']['eraseEndExclusive'] == 0x74000
    assert plan['target']['markerAddress'] == 0x40008
    assert plan['confirmationTokens']['stage'].startswith('STAGE-B-FULL-')


@pytest.mark.parametrize('argv', [['read', '--json'], ['plan', '--slot', 'C', '--json'],
                                  ['--json', 'no-such-command']])
def test_argument_errors_always_have_json(argv, capsys):
    assert tool.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['apiSchema'] == tool.API_SCHEMA and result['errorCode'] == 'INVALID_ARGUMENT'
    assert not result['sideEffectsPossible']


def test_json_never_prompts_even_in_a_tty(tmp_path, capsys, monkeypatch):
    kit, _, session, _, _ = agent_fixture(tmp_path)
    monkeypatch.setattr(tool.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(tool, 'ask', lambda _: pytest.fail('JSON mode must not ask'))
    args = ['stage', '--bundle', str(kit), '--port', 'COM5', '--session', str(session),
            '--target-id', 'fixture-target', '--operation-id', 'stage-1', '--json']
    assert tool.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result['sideEffectsPossible']


def test_completed_operations_are_replayed_without_io(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    first = tool.run_write('flash', kit, 'COM5', token('flash'), operation_id='flash-1', **options)
    snapshot = bytes(chip.flash), chip.restarts, list(chip.erases)
    options['opener'] = lambda _: pytest.fail('duplicate request must not open hardware')
    repeated = tool.run_write('flash', kit, 'COM5', None, operation_id='flash-1', **options)
    assert repeated['replayed'] and repeated['receipts'] == first['receipts']
    assert snapshot == (bytes(chip.flash), chip.restarts, chip.erases)
    with pytest.raises(RecoveryError, match='different arguments'):
        tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='flash-1', **options)
    with pytest.raises(RecoveryError, match='cannot stage'):
        tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='new-id', **options)


def test_separate_activation_never_resets_before_marker_and_is_consumed(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    resets = chip.restarts
    result = tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', hold=True, **options)
    assert chip.restarts == resets and chip.stopped
    assert result['phase'] == 'HELD'
    assert not tool.fresh_stage(kit, tool.load_bundle(kit)[0]['image']['sha256'], 'COM5')
    with pytest.raises(RecoveryError, match='cannot activate'):
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='new-activate', **options)


def test_verified_session_requires_matching_readback_before_activation(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path, baseline=True)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    with pytest.raises(RecoveryError, match='requires a successful'):
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', hold=True, **options)
    readback = full_backup(tmp_path / 'staged', chip.flash)
    result = tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                              baseline=options['baseline'], session=session)
    assert result['phase'] == 'VERIFIED'
    repeated = tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                                baseline=options['baseline'], session=session)
    assert repeated['replayed'] and repeated['phase'] == 'VERIFIED'
    tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', hold=True, **options)
    readback = full_backup(tmp_path / 'activated', chip.flash)
    result = tool.verify_dump(kit, readback, slot='B', full_slot=True, baseline=options['baseline'], session=session)
    assert result['phase'] == 'ACTIVATED_VERIFIED' and not result['bootVerified']
    assert tool.session_status(session)['nextActions'] == ['manual_bench_start']


def test_failed_verification_blocks_activation(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path, baseline=True)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    bad = bytearray(chip.flash)
    bad[0x76000] ^= 1
    readback = full_backup(tmp_path / 'bad', bad)
    with pytest.raises(RecoveryError, match='preserved'):
        tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                         baseline=options['baseline'], session=session)
    with pytest.raises(RecoveryError, match='cannot activate'):
        tool.run_write('activate', kit, 'COM5', token('activate'), hold=True, **options)


def test_uncertain_activation_cannot_be_retried_or_bypassed(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    options['opener'] = lambda _: (_ for _ in ()).throw(OSError('USB lost'))
    with pytest.raises(RecoveryError) as error:
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', **options)
    assert error.value.code == 'WRITE_UNCERTAIN' and not error.value.retry_safe
    assert error.value.side_effects
    with pytest.raises(RecoveryError, match='did not complete'):
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', **options)
    with pytest.raises(RecoveryError, match='cannot activate'):
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='new-id', **options)
    options['session'] = tmp_path / 'different-session.json'
    with pytest.raises(RecoveryError, match='owns this port'):
        tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='bypass', **options)
    # A fresh double read proves the staged bytes still exist. Reconciliation
    # permits a NEW activation; the original uncertain request stays blocked.
    options['session'] = session
    options['opener'] = lambda _: ChipPort(chip)
    readback = full_backup(tmp_path / 'reconcile', chip.flash)
    tool.verify_dump(kit, readback, staged=True, session=session, reconcile=True)
    assert tool.session_status(session)['phase'] == 'VERIFIED'
    tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='after-readback', **options)


def test_port_exclusion_and_reset_guard(tmp_path):
    with tool.PortGuard('COM5'):
        with pytest.raises(RecoveryError) as error:
            tool.read_flash('COM5', tmp_path / 'out')
        assert error.value.code == 'PORT_LOCKED'
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    with pytest.raises(RecoveryError, match='reset/halt is forbidden'):
        tool.read_flash('COM5', tmp_path / 'out', halt=True)


def test_reader_preserves_error_diagnostics(tmp_path, monkeypatch):
    import subprocess
    reader = tmp_path / 'reader.py'
    reader.write_bytes(b'fixture')
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    def failed(command, **kwargs):
        return subprocess.CompletedProcess(command, 7, stdout='No target response', stderr='link error')
    with pytest.raises(RecoveryError) as error:
        tool.read_flash('COM5', tmp_path / 'out', reader=reader, runner=failed)
    assert error.value.code == 'READ_FAILED'
    result = tool.read_json(tmp_path / 'out.json')
    assert result['readerRuns'][0]['exitCode'] == 7
    assert 'No target response' in Path(result['readerRuns'][0]['diagnostics']).read_text()


def test_bseed_board_role_mismatch_is_rejected():
    info = tool.check_image(make_image(image_type=65024))
    with pytest.raises(RecoveryError, match='board/role'):
        tool.check_device('bseed-ts011f', info, {'manifest': {'board': 'OUTLET_BSEED_TS011F'},
                                               'roleMatrix': {'role': 'Router'}})
    with pytest.raises(RecoveryError, match='guarded'):
        tool.check_device('generic-tlsr8258', info, {'pinnedSha256': info['sha256']})


def test_stale_successful_read_cannot_undo_a_failed_verification(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path, baseline=True)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    old_good = full_backup(tmp_path / 'old-good', chip.flash)
    corrupted = bytearray(chip.flash)
    corrupted[0x76000] ^= 1
    bad = full_backup(tmp_path / 'new-bad', corrupted)
    with pytest.raises(RecoveryError, match='preserved'):
        tool.verify_dump(kit, bad, staged=True, slot='B', full_slot=True,
                         baseline=options['baseline'], session=session)
    with pytest.raises(RecoveryError) as error:
        tool.verify_dump(kit, old_good, staged=True, slot='B', full_slot=True,
                         baseline=options['baseline'], session=session)
    assert error.value.code == 'STALE_EVIDENCE'


def test_close_is_explicit_and_does_not_claim_hardware_acceptance(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    state = tool.session_status(session)
    with pytest.raises(RecoveryError):
        tool.close_session(session, 'wrong', 'stop')
    snapshot = bytes(chip.flash), chip.restarts
    closed = tool.close_session(session, state['closeConfirmationToken'], 'operator assessed fallback')
    assert closed['phase'] == 'CLOSED' and not closed['closureIsHardwareAcceptance']
    assert snapshot == (bytes(chip.flash), chip.restarts)


def test_dead_lock_release_preserves_evidence_and_never_unlocks_live_owner(tmp_path, monkeypatch):
    guard = tool.PortGuard('COM5')
    guard.__enter__()  # represent a process that crashed without __exit__
    confirmation = 'UNLOCK-' + guard.owner['nonce']
    monkeypatch.setattr(tool, 'process_alive', lambda pid: True)
    with pytest.raises(RecoveryError):
        tool.unlock_port('COM5', confirmation)
    monkeypatch.setattr(tool, 'process_alive', lambda pid: False)
    result = tool.unlock_port('COM5', confirmation)
    assert not guard.path.exists() and Path(result['archive']).exists()
    assert not result['hardwareReconciled']


def test_agent_status_and_capabilities_are_valid_json(tmp_path, capsys):
    assert tool.main(['capabilities', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['jsonNeverPrompts']
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    assert tool.main(['--session', str(session), 'status', '--json']) == 0
    state = json.loads(capsys.readouterr().out)
    assert state['phase'] == 'STAGED' and not state['physicalIdentityVerified']


def test_in_place_mode_cannot_choose_other_slot_or_skip_full_erase(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path, baseline=True)
    options['slot'] = 'A'
    with pytest.raises(RecoveryError) as error:
        tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    assert error.value.code == 'SLOT_SELECTION_UNCONFIRMED'
    options['slot'] = 'B'
    options['full_slot'] = False
    with pytest.raises(RecoveryError) as error:
        tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    assert error.value.code == 'FULL_SLOT_REQUIRED'


def test_metadata_gate_prevents_unlock_replacement_races(tmp_path):
    with tool.MetadataGate('COM5'):
        with pytest.raises(RecoveryError) as error:
            with tool.PortGuard('COM5'):
                pytest.fail('must not create another owner while metadata is locked')
        assert error.value.code == 'PORT_METADATA_BUSY'
    with tool.PortGuard('COM5'):
        pass


def test_copied_session_cannot_steal_port_during_verification(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    copy = tmp_path / 'copied-session.json'
    copy.write_bytes(session.read_bytes())
    readback = full_backup(tmp_path / 'readback', chip.flash)
    with pytest.raises(RecoveryError) as error:
        tool.verify_dump(kit, readback, staged=True, session=copy)
    assert error.value.code == 'SESSION_CONFLICT'
    assert tool.read_json(tool.authority_path('COM5'))['session'] == str(session)


def test_long_read_workflow_uses_fresh_verification_not_old_transmission_time(tmp_path):
    kit, chip, session, options, token = agent_fixture(tmp_path, baseline=True)
    tool.run_write('stage', kit, 'COM5', token('stage'), operation_id='stage-1', **options)
    state = tool.read_json(session)
    state['stageUtc'] = (tool.utc_now() - datetime.timedelta(hours=2)).isoformat()
    tool.atomic_json(session, state)
    readback = full_backup(tmp_path / 'long-read', chip.flash)
    tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                     baseline=options['baseline'], session=session)
    verified_time = tool.read_json(session)['updatedUtc']
    tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                     baseline=options['baseline'], session=session)
    assert tool.read_json(session)['updatedUtc'] == verified_time
    tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', hold=True, **options)
    assert tool.read_json(session)['phase'] == 'HELD'


def test_historical_rollback_accepts_only_its_exact_finalized_payload(tmp_path, monkeypatch):
    image = make_image(version=0x12053021, seed=19)
    ota = make_ota(image)
    pin = dict(tool.R19_RECOVERY, sha256=tool.sha256(image), bytes=len(image), otaSha256=tool.sha256(ota))
    monkeypatch.setitem(tool.DEVICES['bseed-ts011f-pm-r19-recovery'], 'historicalRecovery', pin)
    source = tmp_path / 'r19.bin'
    source.write_bytes(image)
    plan = tool.prepare(out=tmp_path / 'rollback', device='bseed-ts011f-pm-r19-recovery', image_path=source)
    assert plan['label'] == '1.2.5-bseedr19'
    assert plan['source']['historicalRecovery'] == pin
    assert tool.load_bundle(tmp_path / 'rollback')[1] == image
    # The ordinary BSEED path still requires current exact-SHA matrix evidence.
    with pytest.raises(RecoveryError, match='CI evidence'):
        tool.prepare(out=tmp_path / 'ordinary', device='bseed-ts011f', image_path=source)
    source.write_bytes(make_image(version=0x12053021, seed=20))
    with pytest.raises(RecoveryError, match='exact pinned'):
        tool.prepare(out=tmp_path / 'wrong', device='bseed-ts011f-pm-r19-recovery', image_path=source)


def test_historical_rollback_rejects_unknown_ota_without_weakening_current_gate(tmp_path):
    source = tmp_path / 'not-r19.ota'
    source.write_bytes(make_ota(make_image()))
    with pytest.raises(RecoveryError, match='pinned original'):
        tool.prepare(source, tmp_path / 'bad-rollback', device='bseed-ts011f-pm-r19-recovery')


class DiagnosticPort:
    """Independent UART/SWire receive model, including self-echo and replies.

    A bare TX/RX junction echoes only one byte for a read clock. A real target
    extends that into nine UART samples. This distinction is essential here.
    """
    def __init__(self, *, target=True, cpu=5, corrupt_echo=False, changing_flash=False,
                 changing_cpu=False, baud=921600, partial_response=False):
        self.target, self.cpu, self.baud = target, cpu, baud
        self.corrupt_echo, self.changing_flash = corrupt_echo, changing_flash
        self.changing_cpu, self.partial_response = changing_cpu, partial_response
        self.rx, self.words, self.packet = bytearray(), bytearray(), None
        self.reading = None
        self.divider = 0
        self.writes, self.flash_reads = [], []
        self.cpu_reads = 0
        self.fifo, self.selected, self.auto_read = False, False, False
        self.address, self.offset = 0, 0
        self.flash = bytearray(b'\xff' * FLASH_SIZE)
        for slot, base in tool.SLOTS.items():
            self.flash[base:base + 32] = make_image(seed=1 if slot == 'A' else 2)[:32]

    @staticmethod
    def response_word(value):
        result = bytearray()
        for index in range(8):
            mask = 0x20 if index == 0 else 0x10
            result.append(0xFE & ~mask if value & (1 << (7 - index)) else 0xFE)
        result.append(0xFE)
        return bytes(result)

    def value(self):
        if self.reading == 0xB2:
            return self.divider
        if self.reading == 0x602:
            self.cpu_reads += 1
            return 0 if self.changing_cpu and self.cpu_reads > 2 else self.cpu
        assert self.reading == 0x0C and self.fifo and self.selected and self.auto_read
        value = self.flash[self.address + self.offset]
        if self.changing_flash and self.offset == 0 and self.flash_reads.count(self.address) % 2 == 0:
            value ^= 1
        self.offset += 1
        return value

    def apply_write(self, address, data):
        self.writes.append((address, data))
        assert address not in (0x6F, 0x602), 'diagnostic must never reset or halt'
        if address == 0xB2:
            self.divider = data[0]
        elif address == 0xB3:
            self.fifo = data == b'\x80'
        elif address == 0x0D:
            self.selected = data != b'\x01'
            self.auto_read = data == b'\x0a'
        else:
            assert address == 0x0C and self.fifo and self.selected and self.cpu == 5
            assert len(data) == 5 and data[0] == 0x03 and data[-1] == 0, 'read command only'
            self.address = int.from_bytes(data[1:4], 'big')
            assert self.address in (0, 0x40000)
            self.offset = 0
            self.flash_reads.append(self.address)

    def write(self, data):
        data = bytes(data)
        if data == b'\xfe' and self.reading is not None:
            if self.target and self.divider == round(24_000_000 * 2 / self.baud):
                word = self.response_word(self.value())
                self.rx.extend(word[:4] if self.partial_response else word)
            else:
                self.rx.extend(data)  # bare echo is not a target reply
            return len(data)
        self.rx.extend(data if not self.corrupt_echo else bytes(b ^ 1 for b in data))
        self.words.extend(data)
        while len(self.words) >= 10:
            word = bytes(self.words[:10])
            del self.words[:10]
            assert word[-1] == 0xFE and set(word) <= {0x80, 0xFE}
            value = sum(1 << (7 - i) for i in range(8) if word[i + 1] == 0x80)
            if word[0] == 0x80:
                if value == 0xFF:
                    if self.packet:
                        address = int.from_bytes(bytes(self.packet[1:4]), 'big')
                        assert self.packet[4] == 0
                        self.apply_write(address, bytes(self.packet[5:]))
                    self.packet, self.reading = None, None
                else:
                    assert value == 0x5A and self.packet is None
                    self.packet = [value]
            else:
                assert self.packet is not None
                self.packet.append(value)
                if len(self.packet) == 5 and self.packet[4] == 0x80:
                    self.reading = int.from_bytes(bytes(self.packet[1:4]), 'big')
                    self.packet = None
        return len(data)

    def read(self, size):
        result = bytes(self.rx[:size])
        del self.rx[:size]
        return result

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.rx.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def quick_probe(port, baud):
    return tool.SwireDiagnostic(port, baud, sleep=lambda _: None, response_timeout=0.0001)


def test_diagnostic_decoder_for_every_byte():
    for value in range(256):
        assert tool.diagnostic_decode(DiagnosticPort.response_word(value)) == value
    for word in (b'', b'\xfe' * 8, b'\xfe' * 8 + b'\x00'):
        with pytest.raises(RecoveryError):
            tool.diagnostic_decode(word)


def test_resistor_junction_echo_does_not_qualify_target(tmp_path):
    class ContextLoopback(Loopback):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass
    echo = ContextLoopback()
    result = tool.test_link('COM5', tmp_path / 'adapter.json', phase='adapter', duration=0.1, opener=lambda _: echo)
    assert result['status'] == 'ADAPTER_ECHO_OK' and not result['targetReplyVerified']
    assert not result['fullBackupVerified'] and not result['electricalMeasurementsVerified']
    assert result['connection']['targetContacts'] == ['SWS', '3V3', 'GND']
    bare_echo = DiagnosticPort(target=False)
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', tmp_path / 'target.json', phase='target', device='gledopto-gl-sd-301p',
                       opener=lambda _: bare_echo, probe_factory=quick_probe)
    assert error.value.code == 'NO_SWIRE_TARGET_RESPONSE'
    receipt = tool.read_json(tmp_path / 'target.json')
    assert not receipt['targetReplyVerified'] and not bare_echo.flash_reads


@pytest.mark.parametrize('device', ['bseed-ts011f', 'gledopto-gl-sd-301p'])
@pytest.mark.parametrize('baud', [460800, 921600])
def test_single_sws_target_and_flash_probe_without_flash_writes(tmp_path, device, baud):
    port = DiagnosticPort(baud=baud)
    before = bytes(port.flash)
    result = tool.test_link('COM5', tmp_path / 'probe.json', phase='target', device=device, baud=baud,
                            opener=lambda _: port, probe_factory=quick_probe, require_flash_read=True)
    assert result['status'] == 'FLASH_READ_PROBE_OK' and result['targetReplyVerified']
    assert not result['flashWriteAttempted'] and not result['flashWriteTested']
    assert not result['cpuResetRequested'] and not result['cpuHaltRequested']
    assert not result['fullBackupVerified'] and not result['bootVerified']
    assert [h['slot'] for h in result['headers']] == ['A', 'B']
    assert port.flash_reads == [0, 0, 0x40000, 0x40000]
    assert bytes(port.flash) == before


@pytest.mark.parametrize('cpu', [0, 0xFF, 7])
def test_running_or_unknown_cpu_never_touches_flash_controller(tmp_path, cpu):
    port = DiagnosticPort(cpu=cpu)
    result = tool.test_link('COM5', tmp_path / 'registers.json', phase='target', device='bseed-ts011f',
                            opener=lambda _: port, probe_factory=quick_probe)
    assert result['status'] == 'SWIRE_RESPONSE_OK' and not result['cpuObservedHalted']
    assert not port.flash_reads and all(address == 0xB2 for address, _ in port.writes)
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', tmp_path / 'strict.json', phase='target', device='bseed-ts011f',
                       opener=lambda _: port, probe_factory=quick_probe, require_flash_read=True)
    assert error.value.code == 'TARGET_NOT_HALTED'


@pytest.mark.parametrize('fault,code', [('corrupt_echo', 'UART_ECHO_MISMATCH'),
                                      ('changing_flash', 'UNSTABLE_FLASH_READ'),
                                      ('changing_cpu', 'TARGET_STATE_CHANGED'),
                                      ('partial_response', 'NO_SWIRE_TARGET_RESPONSE')])
def test_probe_stops_on_bad_feedback(tmp_path, fault, code):
    port = DiagnosticPort(**{fault: True})
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', tmp_path / 'fault.json', phase='target', device='bseed-ts011f',
                       opener=lambda _: port, probe_factory=quick_probe)
    assert error.value.code == code
    assert tool.read_json(tmp_path / 'fault.json')['status'] == 'LINK_TEST_FAILED'
    assert all(address not in (0x602, 0x6F) for address, _ in port.writes)


def test_diagnostic_write_allowlist_cannot_program_flash_or_reset_cpu():
    probe = quick_probe(DiagnosticPort(), 921600)
    for safe in (False, True):
        probe.flash_access_safe = safe
        for address, data in [(0x602, b'\x05'), (0x6F, b'\x20'), (0x0C, b'\x06'),
                              (0x0C, b'\x02\0\0\0\xff'), (0x0C, b'\x20\0\0\0'),
                              (0x0C, b'\x60'), (0x0C, b'\x01\x00'), (0x40000, b'\0')]:
            with pytest.raises(RecoveryError):
                probe.write_register(address, data)


def test_link_cli_validation_and_wiring_json(tmp_path, capsys):
    assert tool.main(['test-link', '--phase', 'target', '--port', 'COM5', '--out', str(tmp_path / 'no-device'), '--json']) == 2
    assert json.loads(capsys.readouterr().out)['errorCode'] == 'INVALID_ARGUMENT'
    assert tool.main(['wiring', '--device', 'gledopto-gl-sd-301p', '--connection', 'cp2102-single-sws', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['connection']['resistorNominalOhms'] == 1200
    assert len(result['connection']['targetContacts']) == 3


def test_adapter_phase_cannot_run_against_active_recovery_session(tmp_path):
    kit, _, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    with pytest.raises(RecoveryError, match='adapter-only'):
        tool.test_link('COM5', tmp_path / 'not-opened', phase='adapter', opener=lambda _: pytest.fail('must not open'))
    assert not (tmp_path / 'not-opened').exists()


def test_diagnostic_receipt_cannot_overwrite_previous_evidence(tmp_path):
    output = tmp_path / 'existing.json'
    output.write_text('retained evidence')
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', output, phase='target', device='bseed-ts011f',
                       opener=lambda _: pytest.fail('must not open'))
    assert error.value.code == 'EVIDENCE_EXISTS'
    assert output.read_text() == 'retained evidence'


def test_serial_control_lines_are_configured_before_open(monkeypatch):
    import types
    observed = {}
    class DeferredPort:
        def __init__(self, **kwargs):
            assert kwargs['port'] is None
            self.__dict__.update(kwargs)

        def open(self):
            observed.update(port=self.port, rts=self.rts, dtr=self.dtr, baud=self.baudrate)
    monkeypatch.setitem(sys.modules, 'serial', types.SimpleNamespace(Serial=DeferredPort))
    tool.open_port('COM5', baud=921600)
    assert observed == {'port': 'COM5', 'rts': False, 'dtr': False, 'baud': 921600}


def test_receipt_collision_after_initial_check_never_opens_port(tmp_path, monkeypatch):
    output = tmp_path / 'contended.json'
    class ContendedGuard:
        def __init__(self, _):
            pass

        def __enter__(self):
            output.write_text('other port evidence', encoding='utf-8')

        def __exit__(self, *_):
            pass
    monkeypatch.setattr(tool, 'PortGuard', ContendedGuard)
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', output, phase='target', device='bseed-ts011f',
                       opener=lambda _: pytest.fail('must not open'))
    assert error.value.code == 'EVIDENCE_EXISTS'
    assert output.read_text(encoding='utf-8') == 'other port evidence'


def test_flash_probe_attempts_fifo_cleanup_after_chip_select_cleanup_error():
    port = DiagnosticPort()
    probe = quick_probe(port, 921600)
    probe.find_divider()
    probe.flash_access_safe = True
    original = probe.write_register
    attempted = []
    def failing_cleanup(address, data):
        attempted.append((address, data))
        if address == 0x0D and data == b'\x01':
            raise RecoveryError('simulated cleanup echo failure', code='UART_ECHO_MISMATCH')
        return original(address, data)
    probe.write_register = failing_cleanup
    before = bytes(port.flash)
    with pytest.raises(RecoveryError):
        probe.flash_header(0)
    assert attempted[-2:] == [(0x0D, b'\x01'), (0xB3, b'\0')]
    assert not port.fifo and bytes(port.flash) == before


def test_target_diagnostic_never_advances_active_recovery_session(tmp_path):
    kit, _, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    before_session = session.read_bytes()
    before_authority = tool.authority_path('COM5').read_bytes()
    result = tool.test_link('COM5', tmp_path / 'target-check.json', phase='target', device='bseed-ts011f',
                            opener=lambda _: DiagnosticPort(), probe_factory=quick_probe)
    assert result['status'] == 'FLASH_READ_PROBE_OK'
    assert session.read_bytes() == before_session
    assert tool.authority_path('COM5').read_bytes() == before_authority


class EchoTimer:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TimedEcho(Loopback):
    def __init__(self, timer, *, corrupt=False, lose=0, short_write=False):
        super().__init__(lose=lose)
        self.timer, self.corrupt, self.short_write = timer, corrupt, short_write
        self.transmitted = bytearray()

    def write(self, data):
        self.timer.now += 0.01
        self.transmitted.extend(data)
        count = super().write(data)
        if self.corrupt:
            self.buffer = bytes([self.buffer[0] ^ 1]) + self.buffer[1:]
        return count - 1 if self.short_write else count

    def read(self, size):
        self.timer.now += 0.01
        return super().read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_echo_benchmark_measures_verified_rate_and_chunk_latency():
    timer = EchoTimer()
    port = TimedEcho(timer)
    metrics, events = {}, []
    tool.benchmark_echo(port, metrics, baud=921600, duration=6,
                        clock=timer, progress=lambda event, *_args, **_kwargs: events.append(event))
    assert metrics['chunksVerified'] >= 255  # exercise the complete rotating pattern
    assert metrics['verifiedBytes'] == metrics['chunksVerified'] * 512 == len(port.transmitted)
    assert metrics['verifiedEchoBytesPerSecond'] == pytest.approx(25600)
    assert metrics['chunkRoundTripMs'] == {'min': 20, 'mean': 20, 'max': 20}
    assert metrics['theoreticalUartBytesPerSecond'] == 92160
    assert metrics['missingBytes'] == metrics['mismatchedBytes'] == metrics['timeoutCount'] == 0
    assert metrics['stoppedBy'] == 'DURATION_COMPLETE' and 0x80 not in port.transmitted
    assert events[0] == 'echo-start' and events[-1] == 'echo-summary' and events.count('echo-progress') >= 10


@pytest.mark.parametrize('options,code', [({'corrupt': True}, 'ADAPTER_ECHO_MISMATCH'),
                                         ({'lose': 1}, 'ADAPTER_ECHO_TIMEOUT'),
                                         ({'short_write': True}, 'UART_SHORT_WRITE')])
def test_echo_benchmark_retains_failure_metrics_and_stops(options, code):
    timer, metrics, events = EchoTimer(), {}, []
    port = TimedEcho(timer, **options)
    with pytest.raises(RecoveryError) as error:
        tool.benchmark_echo(port, metrics, baud=460800, duration=5, clock=timer,
                            progress=lambda event, *_args, **_kwargs: events.append(event))
    assert error.value.code == metrics['stoppedBy'] == code
    assert metrics['chunksVerified'] == 0 and len(port.transmitted) == 512
    assert metrics['elapsedSeconds'] > 0 and events[-1] == 'echo-summary'
    if code != 'UART_SHORT_WRITE':
        assert metrics['firstMismatch']['offset'] == 0
        assert metrics['mismatchedBytes'] or metrics['missingBytes']


def test_json_cli_shows_live_debug_on_stderr_and_single_result_on_stdout(tmp_path, monkeypatch, capsys):
    timer = EchoTimer()
    port = TimedEcho(timer)
    monkeypatch.setattr(tool, 'open_port', lambda *_args, **_kwargs: port)
    original = tool.benchmark_echo
    monkeypatch.setattr(tool, 'benchmark_echo', lambda *args, **kwargs: original(*args, **kwargs, clock=timer))
    output = tmp_path / 'live-debug.json'
    assert tool.main(['test-link', '--phase', 'adapter', '--port', 'COM5', '--duration', '0.1',
                      '--debug', '--out', str(output), '--json']) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result['status'] == 'ADAPTER_ECHO_OK' and result['statistics']['verifiedBytes'] > 0
    assert 'B/s' in captured.err and 'chunk' in captured.err and '0 errors' in captured.err
    assert tool.read_json(output)['statistics'] == result['statistics']
    assert any(event['event'] == 'echo-progress' for event in result['events'])


@pytest.mark.parametrize('duration', ['nan', 'inf', '-1', '0', '31'])
def test_invalid_stress_duration_is_rejected_before_port_access(tmp_path, monkeypatch, capsys, duration):
    monkeypatch.setattr(tool, 'open_port', lambda *_args, **_kwargs: pytest.fail('must not open'))
    assert tool.main(['test-link', '--phase', 'adapter', '--port', 'COM5', '--duration', duration,
                      '--out', str(tmp_path / 'invalid.json'), '--json']) == 2
    assert json.loads(capsys.readouterr().out)['errorCode'] == 'INVALID_ARGUMENT'
    assert not (tmp_path / 'invalid.json').exists()


def test_target_progress_reports_scan_cpu_headers_and_summary(tmp_path):
    messages = []
    result = tool.test_link('COM5', tmp_path / 'target-debug.json', phase='target', device='bseed-ts011f',
                            opener=lambda _: DiagnosticPort(), probe_factory=quick_probe,
                            out=messages.append, debug=True)
    assert any('three matching' in line for line in messages)
    assert any('CPU control: 0x05' in line for line in messages)
    assert sum('both reads match' in line for line in messages) == 2
    assert result['statistics']['targetBytesPerSecond'] > 0 and result['elapsedSeconds'] > 0
    assert len(result['events']) <= 200


def test_reader_auto_download_is_verified_and_cache_works_offline(tmp_path, monkeypatch):
    data, calls, messages = b'pinned reader fixture', [], []
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(data))
    def fetch(url, limit):
        calls.append((url, limit))
        return data
    first = tool.ensure_reader(fetch=fetch, out=messages.append)
    second = tool.ensure_reader(fetch=lambda *_: pytest.fail('cache must work offline'))
    assert first['cacheStatus'] == 'DOWNLOADED' and second['cacheStatus'] == 'CACHED'
    assert len(calls) == 1 and calls[0] == (tool.READER_URL, 256000)
    assert Path(first['reader']).read_bytes() == data and not first['hardwareAccessed']
    assert any('SHA-256 verified' in message for message in messages)
    Path(first['reader']).write_bytes(b'corrupt cache')
    with pytest.raises(RecoveryError) as error:
        tool.ensure_reader(fetch=lambda *_: pytest.fail('must not silently replace corrupt cache'))
    assert error.value.code == 'READER_HASH_MISMATCH'


@pytest.mark.parametrize('fault', ['wrong-hash', 'oversize', 'offline'])
def test_reader_download_failure_never_caches_unverified_source(monkeypatch, fault):
    def fetch(*_):
        if fault == 'offline':
            raise OSError('network unavailable')
        return b'x' * (256001 if fault == 'oversize' else 10)
    with pytest.raises(RecoveryError) as error:
        tool.ensure_reader(fetch=fetch)
    assert error.value.code == ('READER_DOWNLOAD_FAILED' if fault == 'offline' else 'READER_HASH_MISMATCH')
    assert not list(tool.STATE_ROOT.rglob('*.py')) and not list(tool.STATE_ROOT.rglob('*.tmp'))


def test_read_automatically_fetches_reader_before_first_serial_process(tmp_path, monkeypatch):
    import subprocess
    source, events = b'verified reader fixture', []
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(source))
    def fetch(*_):
        events.append('download')
        return source
    def run(command, **_kwargs):
        events.append('read')
        assert Path(command[1]).read_bytes() == source and 'rf' in command
        Path(command[-1]).write_bytes(b'a' * 32)
        return subprocess.CompletedProcess(command, 0)
    result = tool.read_flash('COM5', tmp_path / 'automatic.bin', size=32, fetch=fetch, runner=run)
    assert result['status'] == 'READ_MATCHED' and events == ['download', 'read', 'read']
    assert result['readerSetup']['cacheStatus'] == 'DOWNLOADED'


# ---- More devices: tuya-zigbee-switch boards (Avatto ...), BSEED TS0726, Gledopto ED-008 ----

UPSTREAM = 'https://github.com/romasku/tuya-zigbee-switch/raw/0f2b99e5a0ebd72a2d7c3375cde69db4696e6f00'
ED008_SHA = '8d90c745734e38c8686a8649bd859c77508bfc3c453aef43f3bf379e694837ff'


def avatto_ota(**kwargs):
    kwargs.setdefault('image_type', 43527)
    kwargs.setdefault('version', 0x11023000)
    return make_ota(make_image(0x5432, **kwargs))


def index_fixture(tmp_path, ota, *, board='MODULE_AVATTO_A_TS0001', role='router', change=None):
    """An OTA index shaped like zigbee2mqtt/ota/index_<role>.json, with its distractors."""
    folder = board + ('_END_DEVICE' if role == 'end_device' else '')
    manufacturer, image_type, version = struct.unpack_from('<HHI', ota, 10)   # OTA header, not the image
    entry = {'fileName': 'tlc_switch-1.1.2-8542fc05.zigbee', 'fileVersion': version,
             'fileSize': len(ota), 'url': f'{UPSTREAM}/bin/{role}/{folder}/tlc_switch-1.1.2-8542fc05.zigbee',
             'imageType': image_type, 'manufacturerCode': manufacturer,
             'sha512': hashlib.sha512(ota).hexdigest(), 'manufacturerName': ['_TZ3000_4rbqgcuv', '4rbqgcuv']}
    if change:
        change(entry)
    stock = dict(entry, fileName='tlc_switch-1.1.2-8542fc05-from_tuya.zigbee', imageType=54179,
                 fileVersion=0xFFFFFFFF, sha512='0' * 128)
    other = dict(entry, url=entry['url'].replace(f'/{folder}/', '/MODULE_AVATTO_A_TS0002/'),
                 sha512='1' * 128, manufacturerName=['_TZ3000_mtnpt6ws'])
    path = tmp_path / f'index_{role}.json'
    path.write_text(json.dumps([stock, entry, other]))
    return path, entry


def serve(ota, calls=None):
    def fetch(url, limit):
        if calls is not None:
            calls.append((url, limit))
        return ota
    return fetch


def test_avatto_board_resolves_the_released_image_from_the_index(tmp_path):
    ota = avatto_ota()
    index, entry = index_fixture(tmp_path, ota)
    calls = []
    plan = tool.prepare(out=tmp_path / 'kit', device='tuya-zigbee-switch', board='MODULE_AVATTO_A_TS0001',
                        index=index, fetch=serve(ota, calls))
    assert calls == [(entry['url'], len(ota))], 'downloads exactly the indexed file'
    assert plan['source']['index']['board'] == 'MODULE_AVATTO_A_TS0001'
    assert plan['source']['index']['sha512'] == entry['sha512']
    assert plan['label'] == 'MODULE_AVATTO_A_TS0001 router, tlc_switch-1.1.2-8542fc05.zigbee'
    loaded, image = tool.load_bundle(tmp_path / 'kit')
    assert image == ota[62:] and loaded == plan
    # Offline: the same file given locally is checked against the same SHA-512.
    (tmp_path / 'local.ota').write_bytes(ota)
    tool.prepare(tmp_path / 'local.ota', tmp_path / 'kit2', device='tuya-zigbee-switch',
                 board='MODULE_AVATTO_A_TS0001', index=index, fetch=lambda *_: pytest.fail('must stay offline'))
    # The bundle writes like any other: slot A rewritten, the rest of flash kept.
    before = bricked_flash()
    chip = Chip(before)
    writer = writer_for(chip)
    writer.stage(image)
    writer.activate()
    assert_recovered(chip, before, image)


def test_end_device_boards_use_their_own_folder(tmp_path):
    ota = avatto_ota(image_type=43537)
    index, _ = index_fixture(tmp_path, ota, board='MODULE_AVATTO_TS0011', role='end_device')
    plan = tool.prepare(out=tmp_path / 'kit', device='tuya-zigbee-switch', board='MODULE_AVATTO_TS0011',
                        role='end_device', index=index, fetch=serve(ota))
    assert plan['source']['index']['role'] == 'end_device'
    with pytest.raises(RecoveryError, match='lists 0 released'):
        tool.prepare(out=tmp_path / 'kit2', device='tuya-zigbee-switch', board='MODULE_AVATTO_TS0011',
                     role='router', index=index, fetch=serve(ota))


def flipped(ota):
    data = bytearray(ota)
    data[0x2000] ^= 1
    return bytes(data)


def foreign_ota():
    body = bytearray(make_image(0x5432, image_type=43527))
    body[6:8] = b'\0\0'                               # not a Telink image (e.g. an EFR32 board)
    return make_ota(bytes(body))


INDEX_FAULTS = {
    'tampered_download': ({'fetch': lambda ota: serve(flipped(ota))}, 'SHA-512'),
    'unknown_board': ({'board': 'MODULE_AVATTO_TS0011'}, 'lists 0 released'),
    'bseed_socket': ({'board': 'OUTLET_BSEED_PM_TS011F_1'}, 'use --device bseed-ts011f'),
    'ts0726_3gang': ({'board': 'SWITCH_BSEED_TS0726_3GANG'}, 'use --device bseed-ts0726'),
    'bad_name': ({'board': '../MODULE_AVATTO_A_TS0001'}, '--board must'),
    'no_board': ({'board': None}, '--board must'),
    'bare_image': ({'image_path': 'image.bin'}, 'not --image'),
    'bad_role': ({'role': 'coordinator'}, '--role must'),
    'index_version': ({'change': lambda e: e.update(fileVersion=1)}, 'fileVersion'),
    'index_size': ({'change': lambda e: e.update(fileSize=0)}, 'usable SHA-512'),
    'foreign_chip': ({'ota': foreign_ota()}, 'Silicon Labs'),
    'other_manufacturer': ({'ota': avatto_ota(manufacturer=0x124F)}, 'manufacturer'),
}


@pytest.mark.parametrize('fault', sorted(INDEX_FAULTS))
def test_board_profile_refuses_anything_but_the_indexed_release(tmp_path, fault):
    options, message = INDEX_FAULTS[fault]
    ota = options.get('ota') or avatto_ota()
    index, _ = index_fixture(tmp_path, ota, change=options.get('change'))
    fetch = options['fetch'](ota) if 'fetch' in options else serve(ota)
    (tmp_path / 'image.bin').write_bytes(ota[62:])
    image_path = tmp_path / options['image_path'] if 'image_path' in options else None
    with pytest.raises(RecoveryError, match=re.escape(message)):
        tool.prepare(out=tmp_path / 'kit', device='tuya-zigbee-switch', image_path=image_path,
                     board=options.get('board', 'MODULE_AVATTO_A_TS0001'), role=options.get('role'),
                     index=index, fetch=fetch)
    assert not (tmp_path / 'kit').exists()


def bseed_index(tmp_path, ota):
    info = tool.check_image(ota[62:])
    base = 'https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/bseed/'
    entry = {'fileName': 'ts0726-v1102300a.ota', 'fileVersion': info['fileVersion'], 'fileSize': len(ota),
             'url': base + 'ts0726-v1102300a.ota', 'imageType': info['imageType'],
             'manufacturerCode': info['manufacturerCode'], 'sha512': hashlib.sha512(ota).hexdigest(),
             'manufacturerName': ['iedhxgyi']}
    entries = [entry, dict(entry, fileName='ts0726-v1102300a-from_tuya.ota', imageType=54179, sha512='0' * 128),
               dict(entry, fileName='ts011f-pm-v12053007.ota', imageType=43556, sha512='1' * 128)]
    path = tmp_path / 'index_bseed.json'
    path.write_text(json.dumps(entries))
    return path, entry


def test_bseed_ts0726_three_gang_uses_the_fork_release(tmp_path):
    ota = make_ota(make_image(0x5432, image_type=45577, version=0x1102300A))
    index, entry = bseed_index(tmp_path, ota)
    calls = []
    plan = tool.prepare(out=tmp_path / 'kit', device='bseed-ts0726', index=index, fetch=serve(ota, calls))
    assert calls == [(entry['url'], len(ota))]
    assert plan['source']['index']['board'] == 'SWITCH_BSEED_TS0726_3GANG'
    assert tool.load_bundle(tmp_path / 'kit')[1] == ota[62:]
    with pytest.raises(RecoveryError, match='Router image only'):
        tool.prepare(out=tmp_path / 'kit2', device='bseed-ts0726', role='end_device', index=index, fetch=serve(ota))
    # The board, not the image type, names the firmware line: upstream reuses 45577 for
    # another board, which the generic profile accepts, while the 3-gang name is refused.
    info = tool.check_image(ota[62:])
    assert tool.check_device('tuya-zigbee-switch', info,
                             {'index': {**entry, 'board': 'MODULE_TUYA_NOVATO_QS_S05_TS0001',
                                        'role': 'router', 'nativeSha256': info['sha256'],
                                        'url': UPSTREAM + '/bin/router/MODULE_TUYA_NOVATO_QS_S05_TS0001/' + entry['fileName']}})
    with pytest.raises(RecoveryError, match='use --device bseed-ts0726'):
        tool.check_device('tuya-zigbee-switch', info, {'index': {**entry, 'board': 'SWITCH_BSEED_TS0726_3GANG'}})


def test_bundle_with_edited_index_board_is_refused(tmp_path):
    ota = avatto_ota()
    index, _ = index_fixture(tmp_path, ota)
    tool.prepare(out=tmp_path / 'kit', device='tuya-zigbee-switch', board='MODULE_AVATTO_A_TS0001',
                 index=index, fetch=serve(ota))
    plan_path = tmp_path / 'kit' / tool.BUNDLE_PLAN
    plan = json.loads(plan_path.read_text())
    plan['source']['index']['board'] = 'OUTLET_BSEED_PM_TS011F'
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(RecoveryError, match='bseed-ts011f'):
        tool.load_bundle(tmp_path / 'kit')
    del plan['source']['index']
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(RecoveryError, match='OTA index'):
        tool.load_bundle(tmp_path / 'kit')


def test_boards_search_by_zigbee_manufacturer_name(tmp_path, capsys):
    index, _ = index_fixture(tmp_path, avatto_ota())
    entries = json.loads(index.read_text())
    assert [row['board'] for row in tool.board_list(entries, 'router', '4RBQGCUV')] == ['MODULE_AVATTO_A_TS0001']
    assert [row['board'] for row in tool.board_list(entries, 'router')] == [
        'MODULE_AVATTO_A_TS0001', 'MODULE_AVATTO_A_TS0002']
    assert tool.board_list(entries, 'end_device') == []
    assert tool.main(['boards', '--index', str(index), '--search', 'mtnpt6ws', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert [row['board'] for row in result['boards']] == ['MODULE_AVATTO_A_TS0002']


def test_index_options_only_apply_to_index_profiles(tmp_path, capsys):
    ci_artifact(tmp_path, make_image())
    assert tool.main(['prepare', '--ota', str(tmp_path / 'forward.ota'), '--out', str(tmp_path / 'kit'),
                      '--board', 'MODULE_AVATTO_A_TS0001', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['errorCode'] == 'INVALID_ARGUMENT'


def test_gledopto_ed008_is_admitted_only_by_its_exact_bytes():
    info = tool.check_image(make_image(manufacturer=0x124F, image_type=0x1416, version=0x7F090001))
    with pytest.raises(RecoveryError, match='quarantined'):
        tool.check_device('gledopto-gl-sd-301p', info, {'buildManifest': {'DEV_IDENTITY': 'GLSD-ED-009'}})
    info['sha256'] = ED008_SHA
    assert tool.check_device('gledopto-gl-sd-301p', info, {})
    assert tool.image_label('gledopto-gl-sd-301p', info, {}).startswith('GLSD-ED-008')
    assert 'pair the dimmer again' in tool.wiring('gledopto-gl-sd-301p')


def test_generic_profile_points_tuya_images_to_their_profile():
    info = tool.check_image(make_image(image_type=43527))
    with pytest.raises(RecoveryError, match='tuya-zigbee-switch --board'):
        tool.check_device('generic-tlsr8258', info, {'pinnedSha256': info['sha256']})


def test_downloads_are_limited_to_github_sources(monkeypatch):
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    seen = []
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, timeout: seen.append(url) or Response(b'abc'))
    for url in (UPSTREAM + '/bin/router/X/a.zigbee', tool.INDEX_BASE + 'index_router.json'):
        assert tool.download(url, 10) == b'abc'
    for url in ('https://example.com/a.ota', 'http://raw.githubusercontent.com/a',
                'https://github.com/romasku/tuya-zigbee-switch/raw/main/bin/a.zigbee'):
        with pytest.raises(RecoveryError, match='refusing'):
            tool.download(url, 10)
    assert len(seen) == 2


def test_every_device_profile_has_complete_wiring():
    for device_id, device in tool.DEVICES.items():
        assert [pad for pad, _ in device['pads']] == ['SWS', 'GND', '3V3'], device_id
        text = tool.wiring(device_id)
        assert device['name'] in text and 'Continuity' in text


def test_ts0726_conflicting_board_is_refused_before_index_or_image_access(tmp_path):
    with pytest.raises(RecoveryError, match='--board conflicts'):
        tool.prepare(out=tmp_path / 'kit', device='bseed-ts0726', board='SWITCH_BSEED_TS0726_1GANG',
                     fetch=lambda *_: pytest.fail('must refuse before fetching'))
    assert not (tmp_path / 'kit').exists()


@pytest.mark.parametrize('profile,change', [
    ('tuya-zigbee-switch', {'board': 'MODULE_AVATTO_A_TS0002'}),
    ('tuya-zigbee-switch', {'role': 'end_device'}),
    ('tuya-zigbee-switch', {'url': UPSTREAM + '/bin/router/OUTLET_BSEED_PM_TS011F/other.ota'}),
    ('tuya-zigbee-switch', {'fileName': 'other.ota'}),
    ('tuya-zigbee-switch', {'nativeSha256': '0' * 64}),
    ('tuya-zigbee-switch', {'url': 'https://example.com/bin/router/MODULE_AVATTO_A_TS0001/a.ota'}),
    ('bseed-ts0726', {'board': 'SWITCH_BSEED_TS0726_1GANG'}),
    ('bseed-ts0726', {'role': 'end_device'}),
    ('bseed-ts0726', {'url': 'https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/'
                              'zigbee2mqtt/ota/bseed/ts011f-other.ota', 'fileName': 'ts011f-other.ota'}),
])
def test_reopened_index_bundle_refuses_contradictory_board_role_and_provenance(tmp_path, profile, change):
    if profile == 'bseed-ts0726':
        ota = make_ota(make_image(0x5432, image_type=45577, version=0x1102300A))
        index, _ = bseed_index(tmp_path, ota)
        options = {}
    else:
        ota = avatto_ota()
        index, _ = index_fixture(tmp_path, ota)
        options = {'board': 'MODULE_AVATTO_A_TS0001'}
    kit = tmp_path / 'kit'
    tool.prepare(out=kit, device=profile, index=index, fetch=serve(ota), **options)
    path = kit / tool.BUNDLE_PLAN
    plan = tool.read_json(path)
    plan['source']['index'].update(change)
    path.write_text(json.dumps(plan))
    with pytest.raises(RecoveryError):
        tool.load_bundle(kit)


def test_index_entry_wrong_board_url_cannot_prepare_even_when_hash_matches(tmp_path):
    ota = avatto_ota()
    index, _ = index_fixture(tmp_path, ota)
    entries = tool.read_json(index)
    entries[1]['fileName'] = 'other-native.ota'
    index.write_text(json.dumps(entries))
    with pytest.raises(RecoveryError, match='URL/fileName'):
        tool.prepare(out=tmp_path / 'kit', device='tuya-zigbee-switch', board='MODULE_AVATTO_A_TS0001',
                     index=index, fetch=serve(ota))
    assert not (tmp_path / 'kit').exists()


USB_ROW = {'device': 'COM5', 'description': 'fixture USB UART', 'hwid': 'fixture',
           'vid': 1, 'pid': 2, 'serialNumber': 'fixture', 'location': 'fixture-location'}


def watch_timer():
    timer = EchoTimer()
    def sleep(seconds):
        timer.now += seconds
    return timer, sleep


@pytest.mark.parametrize('where,expected', [('open', 'SERIAL_OPEN_FAILED'), ('probe', 'SERIAL_IO_FAILED')])
def test_serial_faults_are_distinct_from_target_timeouts(tmp_path, where, expected):
    class BrokenPort(DiagnosticPort):
        def write(self, data):
            raise OSError(31, 'fixture device not functioning')
    def opening(_):
        if where == 'open':
            raise OSError(31, 'fixture device not functioning')
        return BrokenPort()
    path = tmp_path / 'fault.json'
    with pytest.raises(RecoveryError) as error:
        tool.test_link('COM5', path, phase='target', device='gledopto-gl-sd-301p',
                       opener=opening, probe_factory=quick_probe)
    assert error.value.code == expected
    result = tool.read_json(path)
    assert result['errorCode'] == expected and result['errorDetails']['exceptionType'] == 'OSError'
    assert not result['flashWriteAttempted'] and not result['cpuResetRequested']


def test_echo_only_reads_explain_missing_target_samples(tmp_path):
    path = tmp_path / 'echo-only.json'
    messages = []
    with pytest.raises(RecoveryError):
        tool.test_link('COM5', path, phase='target', device='gledopto-gl-sd-301p', debug=True,
                       out=messages.append, opener=lambda _: DiagnosticPort(target=False), probe_factory=quick_probe)
    result = tool.read_json(path)
    assert result['statistics']['targetBytesDecoded'] == 0
    assert result['statistics']['echoOnlyReads'] > 0
    assert result['statistics']['lastTargetResponse'] == {'expectedBytes': 9, 'receivedBytes': 1}
    assert any('1/9 UART samples (adapter echo only)' in message for message in messages)


def test_no_more_serial_cleanup_traffic_after_read_io_failure():
    class BrokenRead(DiagnosticPort):
        def read(self, size):
            if size == 9:
                raise OSError('fixture disconnected during response')
            return super().read(size)
    port = BrokenRead()
    probe = quick_probe(port, 921600)
    with pytest.raises(OSError, match='disconnected during response'):
        probe.find_divider()
    assert probe.stats['cleanupSkippedAfterSerialFailure']
    assert port.reading == 0xB2  # no stop-word transmission attempted after the I/O fault


def test_watch_waits_for_initial_usb_and_only_runs_target_probe(tmp_path):
    timer, sleep = watch_timer()
    calls, messages = [], []
    port = DiagnosticPort()
    before = bytes(port.flash)
    def inventory():
        return [USB_ROW] if timer.now >= 2 else []
    def probe(name, receipt, **kwargs):
        calls.append((timer.now, name, kwargs['phase']))
        return tool.test_link(name, receipt, **kwargs, opener=lambda _: port, probe_factory=quick_probe)
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=10,
                             inventory=inventory, probe=probe, clock=timer, sleep=sleep, out=messages.append)
    assert result['status'] == 'TARGET_REPLIES_VERIFIED'
    assert calls == [(2, 'COM5', 'target')] and result['adapterIdentity']['serialNumber'] == 'fixture'
    assert bytes(port.flash) == before and not result['flashWriteAttempted']
    assert not result['cpuResetRequested'] and not result['cpuHaltRequested']
    assert tool.read_json(tmp_path / 'watch/summary.json')['successfulReceipt']


def test_watch_backoff_does_not_reopen_broken_driver_every_second(tmp_path):
    timer, sleep = watch_timer()
    calls = []
    def probe(name, receipt, **kwargs):
        calls.append(timer.now)
        raise RecoveryError('fixture Windows error 31', code='SERIAL_OPEN_FAILED')
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=30,
                             inventory=lambda: [USB_ROW], probe=probe, clock=timer, sleep=sleep)
    assert result['status'] == 'WATCH_TIMEOUT'
    assert calls == [0, 2, 6, 14, 24]
    assert len(result['events']) < 20 and not result['targetReplyVerified']


def test_watch_follows_same_usb_after_renumbering_not_unrelated_com_port(tmp_path):
    timer, sleep = watch_timer()
    calls = []
    def inventory():
        if not calls:
            return [USB_ROW]
        if timer.now < 1:
            return []
        return [{**USB_ROW, 'serialNumber': 'unrelated'}, {**USB_ROW, 'device': 'COM7'}]
    def probe(name, receipt, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            raise RecoveryError('fixture disappeared', code='SERIAL_IO_FAILED')
        return {'status': 'SWIRE_RESPONSE_OK', 'targetReplyVerified': True, 'proofLevel': 'target-register-readback'}
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=10,
                             inventory=inventory, probe=probe, clock=timer, sleep=sleep)
    assert calls == ['COM5', 'COM7'] and result['port'] == 'COM7'
    assert result['status'] == 'TARGET_REPLIES_VERIFIED'


@pytest.mark.parametrize('code', ['PORT_LOCKED', 'INVALID_PHASE', 'LINK_TEST_INTERRUPTED'])
def test_watch_never_bypasses_lock_or_repeats_after_interrupt(tmp_path, code, monkeypatch):
    timer, sleep = watch_timer()
    calls = []
    monkeypatch.setattr(tool, 'unlock_port', lambda *_: pytest.fail('must never unlock'))
    def probe(name, receipt, **kwargs):
        calls.append(name)
        raise RecoveryError('fixture stop', code=code)
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=10,
                             inventory=lambda: [USB_ROW], probe=probe, clock=timer, sleep=sleep)
    assert calls == ['COM5']
    assert result['status'] == ('WATCH_INTERRUPTED' if code == 'LINK_TEST_INTERRUPTED' else 'WATCH_BLOCKED')


def test_watch_ambiguous_adapter_never_opens_port(tmp_path):
    timer, sleep = watch_timer()
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=5,
                             inventory=lambda: [USB_ROW, USB_ROW],
                             probe=lambda *_args, **_kwargs: pytest.fail('must not open'), clock=timer, sleep=sleep)
    assert result['status'] == 'WATCH_BLOCKED' and result['state'] == 'ADAPTER_AMBIGUOUS'


def test_watch_absent_usb_honours_window_without_serial_access(tmp_path):
    timer, sleep = watch_timer()
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=5,
                             inventory=lambda: [], probe=lambda *_args, **_kwargs: pytest.fail('must not open'),
                             clock=timer, sleep=sleep)
    assert result['status'] == 'WATCH_TIMEOUT' and timer.now == 5 and not result['attempts']


def test_watch_json_cli_progress_is_stderr_and_output_directory_is_exclusive(tmp_path, monkeypatch, capsys):
    timer, sleep = watch_timer()
    original = tool.watch_link
    def watch(*args, **kwargs):
        return original(*args, **kwargs, inventory=lambda: [], clock=timer, sleep=sleep)
    monkeypatch.setattr(tool, 'watch_link', watch)
    command = ['watch-link', '--device', 'gledopto-gl-sd-301p', '--port', 'COM5', '--seconds', '2',
               '--out', str(tmp_path / 'watch'), '--json']
    assert tool.main(command) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)['status'] == 'WATCH_TIMEOUT' and 'Waiting' in captured.err
    assert tool.main(command) == 2
    assert json.loads(capsys.readouterr().out)['errorCode'] == 'EVIDENCE_EXISTS'


def halt_fixture(tmp_path, monkeypatch, *, outcomes):
    """Pinned reader fixture plus a scripted CPU pause check (no hardware)."""
    import subprocess
    reader = tmp_path / 'reader.py'
    reader.write_bytes(b'pinned source fixture')
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    events = []

    def halt_check(port, path, _out):
        events.append('halt')
        outcome = outcomes.pop(0)
        if outcome != 'paused':
            raise RecoveryError('CPU pause not confirmed', code=outcome)
        return {'divider': 52, 'cpuControls': [5, 5], 'haltVerified': True}

    def run(command, **_kwargs):
        events.append('read')
        assert not {'-t', 'ea', 'es', 'wf', 'we', '-r'} & set(command)
        Path(command[-1]).write_bytes(b'a' * 32)
        return subprocess.CompletedProcess(command, 0)
    return reader, events, halt_check, run


def test_halt_read_proves_the_pause_before_each_read_and_never_passes_reset(tmp_path, monkeypatch):
    reader, events, halt_check, run = halt_fixture(tmp_path, monkeypatch, outcomes=['paused', 'paused'])
    result = tool.read_flash('COM5', tmp_path / 'flash.bin', size=32, reader=reader, halt=True,
                             halt_check=halt_check, runner=run)
    assert result['status'] == 'READ_MATCHED' and events == ['halt', 'read', 'halt', 'read']
    assert [check['cpuControls'] for check in result['haltChecks']] == [[5, 5], [5, 5]]


def test_single_read_after_a_write_is_never_a_baseline(tmp_path, monkeypatch):
    reader, events, halt_check, run = halt_fixture(tmp_path, monkeypatch, outcomes=['paused'])
    target = tmp_path / 'after.bin'
    result = tool.read_flash('COM5', target, size=32, reader=reader, halt=True, reads=1,
                             halt_check=halt_check, runner=run)
    assert result['status'] == 'READ_SINGLE' and events == ['halt', 'read']
    assert not target.with_name('after.bin.second.bin').exists()
    with pytest.raises(RecoveryError, match='baseline must be'):
        tool.matched_backup(target)


def scripted_pause(*codes):
    """A pause check that fails with the given codes, then (for 'paused') succeeds."""
    codes = list(codes)
    def check(*_args):
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        if code != 'paused':
            raise RecoveryError('no pause', code=code)
        return {'divider': 52, 'cpuControls': [5, 5], 'haltVerified': True}
    return check


def test_pause_retries_through_a_power_cycle_then_gives_up():
    clock, sleeps, messages = [0.0], [], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    timing = {'clock': lambda: clock[0], 'sleep': sleep}
    record = tool.await_cpu_halted('COM5', Path('r.py'), retry_seconds=60, out=messages.append,
                                   halt_check=scripted_pause('SWIRE_RESPONSE_TIMEOUT', 'TARGET_NOT_HALTED', 'paused'),
                                   **timing)
    assert record['attempts'] == 3 and sleeps == [10, 10]
    assert any('off and on' in message for message in messages)
    clock[0] = 0.0
    with pytest.raises(RecoveryError) as error:
        tool.await_cpu_halted('COM5', Path('r.py'), retry_seconds=15,
                              halt_check=scripted_pause('TARGET_NOT_HALTED'), **timing)
    assert error.value.code == 'TARGET_NOT_HALTED' and len(error.value.details['attempts']) == 2
    with pytest.raises(RecoveryError) as error:
        tool.await_cpu_halted('COM5', Path('r.py'), retry_seconds=300,
                              halt_check=scripted_pause('PORT_LOCKED'), **timing)
    assert error.value.code == 'PORT_LOCKED'


def test_paused_read_allowed_after_a_finished_flash_but_not_while_staged(tmp_path, monkeypatch):
    kit, chip, session, options, token = agent_fixture(tmp_path)
    tool.run_write('stage', kit, 'COM5', token('stage'), **options)
    with pytest.raises(RecoveryError, match='reset/halt is forbidden'):
        tool.read_flash('COM5', tmp_path / 'staged.bin', halt=True)
    assert 'BOOT_REQUESTED' in tool.HALT_READ_PHASES and 'STAGED' not in tool.HALT_READ_PHASES


def test_cli_refuses_retry_without_halt(tmp_path, capsys):
    code = tool.main(['read', '--json', '--port', 'COM5', '--out', str(tmp_path / 'x.bin'), '--retry-seconds', '60'])
    assert code == 2
    assert json.loads(capsys.readouterr().out)['errorCode'] == 'INVALID_ARGUMENT'


def test_pause_retries_every_real_no_reply_code_and_a_failed_activation():
    # find_divider raises NO_SWIRE_TARGET_RESPONSE when a powered-off board is silent:
    # the exact case --retry-seconds exists for.
    clock, sleeps = [0.0], []
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    codes = ('NO_SWIRE_TARGET_RESPONSE', 'SWIRE_FRAMING', 'UNSTABLE_SWIRE_READBACK', 'UART_ECHO_MISMATCH',
             'LINK_TEST_TIMEOUT', 'HALT_ATTEMPT_FAILED')
    assert set(codes) <= set(tool.HALT_RETRY_CODES)
    record = tool.await_cpu_halted('COM5', Path('r.py'), retry_seconds=120, halt_check=scripted_pause(*codes, 'paused'),
                                   clock=lambda: clock[0], sleep=sleep)
    assert record['attempts'] == len(codes) + 1 and len(sleeps) == len(codes)


def test_upstream_activation_faults_become_a_retryable_structured_error(tmp_path, monkeypatch):
    import contextlib
    class Port:
        write_timeout = None
    @contextlib.contextmanager
    def fake_port(*_args, **_kwargs):
        yield Port()
    for fault in (SystemExit('Chip sleep? -> Use reset chip'), OSError('port vanished'), ValueError('upstream bug')):
        class Pinned:
            @staticmethod
            def activate(*_args):
                raise fault
        monkeypatch.setattr(tool, 'load_pinned_reader', lambda _reader, pinned=Pinned: pinned)
        monkeypatch.setattr(tool, 'open_port', fake_port)
        with pytest.raises(RecoveryError) as error:
            tool.verify_cpu_halted('COM5', tmp_path / 'reader.py')
        assert error.value.code == 'HALT_ATTEMPT_FAILED' and error.value.retry_safe is True


def test_pinned_reader_is_hash_checked_again_right_before_it_runs(tmp_path, monkeypatch):
    reader = tmp_path / 'reader.py'
    reader.write_text('raise SystemExit("must never run")')
    with pytest.raises(RecoveryError) as error:
        tool.load_pinned_reader(reader)
    assert error.value.code == 'READER_HASH_MISMATCH'
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    reader.write_text('VALUE = 1')
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    assert tool.load_pinned_reader(reader).VALUE == 1


def reader_command(script):
    import sys as _sys
    return [_sys.executable, '-c', script]


def test_reader_progress_is_reported_and_a_slow_start_is_not_a_stall(tmp_path):
    messages = []
    script = ('import time\nfor a in (0, 256, 512):\n    print("Read from 0x%06x..." % a, flush=True)\n'
              '    time.sleep(0.4)\n')
    done = tool.run_reader_with_progress(reader_command(script), log_path=tmp_path / 'live.log', size=1024,
                                         label='Read 1/1', out=messages.append, first_line_seconds=10, stall_seconds=5)
    assert done.returncode == 0 and 'Read from 0x000200' in (tmp_path / 'live.log').read_text()
    # A reader that prints nothing for longer than the stall window, but less than the
    # first-line grace, is a slow start and is allowed to finish.
    slow = 'import time\ntime.sleep(2.5)\nprint("Read from 0x000000...", flush=True)\n'
    done = tool.run_reader_with_progress(reader_command(slow), log_path=tmp_path / 'slow.log', size=1024,
                                         label='Read 1/1', first_line_seconds=10, stall_seconds=1)
    assert done.returncode == 0


@pytest.mark.parametrize('prints_first_line', [False, True])
def test_reader_stall_is_stopped_with_its_log_kept(tmp_path, prints_first_line):
    script = ('import time\n' + ('print("Read from 0x000000...", flush=True)\n' if prints_first_line else '') +
              'time.sleep(60)\n')
    with pytest.raises(RecoveryError) as error:
        tool.run_reader_with_progress(reader_command(script), log_path=tmp_path / 'live.log', size=1024,
                                      label='Read 1/1', first_line_seconds=1.5, stall_seconds=1.5)
    assert error.value.code == 'READ_STALLED'
    assert error.value.details['sawFirstAddress'] is prints_first_line
    assert (tmp_path / 'live.log').is_file(), 'live log is kept for diagnosis'


def test_read_refuses_to_overwrite_earlier_live_diagnostics(tmp_path, monkeypatch):
    import subprocess
    reader = tmp_path / 'reader.py'
    reader.write_bytes(b'pinned source fixture')
    monkeypatch.setattr(tool, 'READER_SHA256', tool.sha256(reader.read_bytes()))
    target = tmp_path / 'flash.bin'
    target.with_name('flash.bin.reader-1.live.log').write_text('earlier evidence')
    with pytest.raises(RecoveryError, match='already exists'):
        tool.read_flash('COM5', target, size=32, reader=reader, halt=False,
                        runner=lambda command, **_kw: subprocess.CompletedProcess(command, 0))
    assert target.with_name('flash.bin.reader-1.live.log').read_text() == 'earlier evidence'


@pytest.mark.parametrize('code', ['UART_RESPONSE_LENGTH', 'UART_SHORT_WRITE'])
def test_watch_keeps_going_after_stray_bytes_or_a_short_write(tmp_path, code):
    timer, sleep = watch_timer()
    calls = []
    def probe(name, receipt, **kwargs):
        calls.append(timer.now)
        if len(calls) == 1:
            raise RecoveryError('fixture glitch', code=code)
        return {'status': 'SWIRE_RESPONSE_OK', 'targetReplyVerified': True, 'proofLevel': 'target-register-readback'}
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=10,
                             inventory=lambda: [USB_ROW], probe=probe, clock=timer, sleep=sleep)
    assert len(calls) == 2 and result['status'] == 'TARGET_REPLIES_VERIFIED'


def test_watch_survives_a_truncated_probe_receipt(tmp_path):
    timer, sleep = watch_timer()
    calls = []
    def probe(name, receipt, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            Path(receipt).write_text('{"status": "LINK_TEST_FAIL')  # probe interrupted mid-write
            raise RecoveryError('fixture interrupted write', code='SWIRE_RESPONSE_TIMEOUT')
        return {'status': 'SWIRE_RESPONSE_OK', 'targetReplyVerified': True, 'proofLevel': 'target-register-readback'}
    result = tool.watch_link('COM5', tmp_path / 'watch', device='gledopto-gl-sd-301p', seconds=10,
                             inventory=lambda: [USB_ROW], probe=probe, clock=timer, sleep=sleep)
    assert len(calls) == 2 and result['status'] == 'TARGET_REPLIES_VERIFIED'
    assert result['attempts'][0]['errorCode'] == 'SWIRE_RESPONSE_TIMEOUT'
