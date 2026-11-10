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
def no_waiting(monkeypatch):
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
    assert not chip.stopped and chip.restarts == 3
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
        with pytest.raises(RecoveryError, match='needs a finished stage'):
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
    with pytest.raises(KeyboardInterrupt):
        tool.run_write('stage', kit, 'COM5', f'STAGE-{token}', opener=lambda port: Unplugged(chip),
                       out=lambda _: None)
    assert [entry['result'] for entry in tool.receipts(kit)] == ['STARTED', 'INTERRUPTED: KeyboardInterrupt']
    with pytest.raises(RecoveryError, match='needs a finished stage'):
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
