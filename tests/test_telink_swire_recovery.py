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
        tool.run_write('activate', kit, 'COM5', token('activate'), operation_id='activate-1', **options)
    readback = full_backup(tmp_path / 'staged', chip.flash)
    result = tool.verify_dump(kit, readback, staged=True, slot='B', full_slot=True,
                              baseline=options['baseline'], session=session)
    assert result['phase'] == 'VERIFIED'
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
        tool.run_write('activate', kit, 'COM5', token('activate'), **options)


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
