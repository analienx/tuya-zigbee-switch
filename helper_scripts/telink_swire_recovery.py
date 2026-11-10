#!/usr/bin/env python3
"""Wired recovery of Telink TLSR825x Zigbee devices with a USB-UART adapter.

Writes a firmware image into image slot A (0x00000) of a 512 KiB TLSR8258
without a bootloader. Only the adapter's TXD drives the chip's SWS pin
(pvvx's TX-only SWire method), so the chip never answers: this tool cannot
confirm that flash changed. Success is the device starting afterwards.

Commands
  ports      list serial ports
  selftest   loopback test of the adapter alone (TXD jumpered to RXD)
  prepare    make a recovery bundle from a Zigbee OTA file (offline)
  flash      stage, then activate, in one run
  stage      erase slot A and write the image with its boot marker off
  activate   switch the boot marker on (one byte, no erase) and restart

Never written: 0x34000 and above (Zigbee NVM, slot B, IEEE address,
factory calibration, second NVM area). Needs pyserial for the commands
that open a port: python -m pip install pyserial
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time
import zlib

# Wire timing from pvvx USBCOMFlashTx.html v0.5 (TX-only). The SWS divider is
# tuned for this baud rate; do not change one without the other. The chip
# cannot report busy, so the waits are fixed and longer than pvvx's (300 ms
# erase, 3 ms status) to cover slow SPI NOR status writes and erases.
UART_BAUD = 460800
SWS_DIVIDER = 55
ERASE_WAIT = 0.400
PAGE_WAIT = 0.010
STATUS_WAIT = 0.050
STOP_CPU_SECONDS = 3.0
STAGE_FRESH_SECONDS = 1800

SECTOR = 0x1000
PAGE = 0x100
SLOT_A_END = 0x34000              # Zigbee NVM base, SDK 3.7.2.0 drv_nv.h (512 KiB)
MARKER_OFFSET = 0x08
MARKER = b'KNLT'
ACTIVATE_BYTE = b'K'              # 0xFF -> 0x4B only clears bits, so no erase
TELINK_8258_MAGIC = b'\x5d\x02'
SIZE_OFFSET = 0x18
# A start marker here makes the firmware treat flash as a bootloader-mode or
# small-OTA image and move it (ensure_ota_scheme.c). The image must not carry
# one, and stale flash there is always erased, however short the image.
OTHER_START_MARKERS = (0x8008, 0x20008)
MIN_ERASE_END = 0x21000
OTA_MAGIC = 0x0BEEF11E
OTA_HEADER = 56
# Which passes erase before programming. Three passes survive one lost burst
# anywhere: pass 2 redoes a lost pass-1 erase or page, and pass 3 reprograms a
# page whose pass-2 program was lost after its erase landed. Reprogramming the
# same bytes over correct data changes nothing.
SCHEDULES = {1: (True,), 2: (True, False), 3: (True, True, False)}
PASSES = 3
ACTIVATION_CASES = {'complete', 'partial', 'corrupt-crc'}

BUNDLE_IMAGE = 'image.bin'
BUNDLE_PLAN = 'plan.json'
BUNDLE_LOG = 'receipts.jsonl'
SCHEMA = 1
CHECKLIST = (
    'Board off mains; nothing mains-connected touches the board or the PC.',
    'Adapter in TTL mode; TXD idles at about 3.3 V (measured, not assumed).',
    'TXD -> SWS, GND -> GND, 3V3 -> 3V3 (or a separate 3.3 V supply). RXD unconnected. Never +5V.',
    'Clips hold on their own; nothing bridges neighbouring pads.',
)


class RecoveryError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def telink_crc(data):
    """CRC-32 without the final inversion, as Telink stores it in the image tail."""
    return zlib.crc32(data) ^ 0xFFFFFFFF


def erase_end(length):
    """First address after the sectors stage erases."""
    return max(-(-length // SECTOR) * SECTOR, MIN_ERASE_END)


def check_image(image):
    """Validate a finalized Telink image and describe it."""
    if not 0x20 <= len(image) < SLOT_A_END:
        raise RecoveryError(f'image is {len(image)} bytes; slot A holds at most {SLOT_A_END - 1}')
    if image[6:8] != TELINK_8258_MAGIC:
        raise RecoveryError('not a finalized TLSR8258 image (wrong magic at 0x06); '
                            'use the image inside the OTA file, not forward.bin')
    if image[MARKER_OFFSET:MARKER_OFFSET + 4] != MARKER:
        raise RecoveryError('image has no KNLT start marker at 0x08')
    if u32(image, SIZE_OFFSET) != len(image):
        raise RecoveryError(f'image declares {u32(image, SIZE_OFFSET)} bytes but has {len(image)}')
    if telink_crc(image[:-4]) != u32(image, len(image) - 4):
        raise RecoveryError('image CRC does not match its tail')
    for offset in OTHER_START_MARKERS:
        if image[offset:offset + 4] == MARKER:
            raise RecoveryError(f'image has a second start marker at {offset:#x}')
    return {
        'bytes': len(image),
        'sha256': sha256(image),
        'fileVersion': u32(image, 0x02),
        'manufacturerCode': u16(image, 0x12),
        'imageType': u16(image, 0x14),
        'crc32': f'{u32(image, len(image) - 4):08x}',
    }


def image_from_ota(ota):
    """Return the native image inside a single-image Zigbee OTA file."""
    if len(ota) < OTA_HEADER + 6 or u32(ota, 0) != OTA_MAGIC:
        raise RecoveryError('not a Zigbee OTA file')
    if u16(ota, 6) != OTA_HEADER:
        raise RecoveryError('unsupported OTA header length')
    if u32(ota, 52) != len(ota):
        raise RecoveryError('OTA total size does not match the file')
    tag, size = struct.unpack_from('<HI', ota, OTA_HEADER)
    if tag != 0 or size != len(ota) - OTA_HEADER - 6:
        raise RecoveryError('expected exactly one firmware sub-element')
    image = ota[OTA_HEADER + 6:]
    info = check_image(image)
    header = {'manufacturerCode': u16(ota, 10), 'imageType': u16(ota, 12),
              'fileVersion': u32(ota, 14)}
    for key, value in header.items():
        if value != info[key]:
            # Stock-conversion and FORCE wrappers carry a transport identity.
            raise RecoveryError(f'OTA header {key} {value:#x} differs from the image '
                                f'({info[key]:#x}); use forward.ota, not a wrapper')
    return image, info


def check_manifest(manifest, ota, info):
    entry = manifest.get('artifacts', {}).get('forward.ota', {})
    if entry.get('sha256') != sha256(ota) or entry.get('bytes', len(ota)) != len(ota):
        raise RecoveryError('manifest does not describe this OTA file')
    if manifest.get('sourceDirty') is not False:
        raise RecoveryError('manifest does not show a clean source tree')
    header = manifest.get('otaHeader', {})
    for key in ('manufacturerCode', 'imageType', 'fileVersion'):
        if header.get(key) != info[key]:
            raise RecoveryError(f'manifest otaHeader {key} differs from the image')
    return {key: manifest.get(key) for key in ('board', 'swBuildId', 'sourceCommit', 'buildDate')}


def check_role_matrix(matrix, ota_sha):
    """The checks of bseed_pm_boot.has_tc32_evidence, restated so the kit stays one file."""
    if matrix.get('compiledBothRoles') is not True or matrix.get('reproducedBothRoles') is not True:
        raise RecoveryError('role matrix does not show both roles compiled and reproduced')
    artifacts = matrix.get('artifacts', [])
    entries = [a for a in artifacts if a.get('sha256') == ota_sha]
    if len(entries) != 1:
        raise RecoveryError('role matrix does not list this OTA file exactly once')
    entry = entries[0]
    boot, activation = entry.get('tc32Boot', {}), entry.get('tc32Activation', {})
    if (boot.get('otaSha256') != ota_sha or boot.get('slots') != [0, 0x40000]
            or boot.get('restartsPerSlot', 0) < 1
            or activation.get('activatorOtaSha256') != ota_sha
            or activation.get('stagedOtaSha256') not in {a.get('sha256') for a in artifacts}
            or set(activation.get('staging', [])) != ACTIVATION_CASES
            or activation.get('slotDirections') != 2):
        raise RecoveryError('role matrix has no complete TC32 start and OTA activation evidence for this image')
    return {'role': entry.get('role'), 'build': entry.get('build'),
            'sourceCommit': matrix.get('sourceCommit')}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class TxOnlyWriter:
    """Send-only SWire commands for the TLSR8258 flash controller. Never reads."""

    def __init__(self, port, *, sleep=time.sleep, clock=time.monotonic):
        self.port = port
        self.sleep = sleep
        self.clock = clock
        self.uart_bytes = 0
        self.frames = 0
        self.waited = 0.0
        self.erased = []
        self.pages = 0
        self.cpu_stops = 0

    def wait(self, seconds):
        self.waited += seconds
        self.sleep(seconds)

    def raw(self, buf):
        sent = self.port.write(buf)
        if sent != len(buf):
            raise RecoveryError(f'serial port took {sent} of {len(buf)} bytes')
        self.port.flush()
        self.uart_bytes += sent
        self.frames += 1

    def sws(self, address, data):
        self.raw(swire_frame(address, data))

    def command(self, spi_command):
        self.sws(0x0D, b'\x00')                       # chip select low
        self.sws(0x0C, bytes((spi_command, 1)))       # command, then chip select high

    def restart(self):
        self.sws(0x006F, b'\x20')

    def stop_cpu(self):
        # Restart the chip, then keep asking it to halt while its ROM starts.
        self.restart()
        stop = swire_frame(0x0602, b'\x05')
        start = self.clock()
        while self.clock() - start < STOP_CPU_SECONDS:
            self.raw(stop)
        self.sws(0x00B2, bytes((SWS_DIVIDER,)))
        self.raw(stop)
        self.command(0xAB)                            # release flash from power-down
        self.cpu_stops += 1

    def unprotect(self):
        # pvvx v0.5 writes status 0 twice. Clears any block protection.
        for _ in range(2):
            self.command(0x06)
            self.sws(0x0D, b'\x00')
            self.sws(0x0C, b'\x01')
            self.sws(0x0C, b'\x00\x01')
            self.wait(STATUS_WAIT)

    def erase(self, address):
        if address % SECTOR or not 0 <= address < SLOT_A_END:
            raise RecoveryError(f'refusing to erase {address:#x}: outside slot A')
        self.command(0x06)
        self.sws(0x0D, b'\x00')
        self.sws(0x0C, b'\x20')
        self.sws(0x0C, bytes(((address >> 16) & 0xFF,)))
        self.sws(0x0C, bytes(((address >> 8) & 0xFF,)))
        self.sws(0x0C, bytes((address & 0xFF, 1)))
        self.erased.append(address)
        self.wait(ERASE_WAIT)

    def program(self, address, data, *, marker=False):
        if marker:
            if (address, data) != (MARKER_OFFSET, ACTIVATE_BYTE):
                raise RecoveryError('the marker write can only put 0x4B at 0x08')
        elif (not data or address < 0 or address + len(data) > SLOT_A_END
              or address // PAGE != (address + len(data) - 1) // PAGE):
            raise RecoveryError(f'refusing to program {len(data)} bytes at {address:#x}')
        elif address <= MARKER_OFFSET < address + len(data) and data[MARKER_OFFSET - address] != 0xFF:
            raise RecoveryError('stage must leave the start marker off')
        self.command(0x06)
        self.sws(0x0D, b'\x00')
        self.sws(0x00B3, b'\x80')                     # FIFO mode: all bytes to 0x0C
        self.sws(0x0C, bytes((0x02, (address >> 16) & 0xFF,
                              (address >> 8) & 0xFF, address & 0xFF)) + data)
        self.sws(0x00B3, b'\x00')
        self.sws(0x0D, b'\x01')
        self.pages += 1
        self.wait(PAGE_WAIT)

    def stage(self, image, passes=PASSES):
        """Erase slot A and write the image with the start marker left at 0xFF.

        See SCHEDULES for which passes erase. The erase always reaches
        MIN_ERASE_END so no stale start marker survives at 0x8008 or 0x20008.
        """
        if passes not in SCHEDULES:
            raise RecoveryError(f'passes must be one of {sorted(SCHEDULES)}')
        check_image(image)
        staged = bytearray(image)
        staged[MARKER_OFFSET] = 0xFF
        self.stop_cpu()
        self.unprotect()
        for erasing in SCHEDULES[passes]:
            for address in range(0, erase_end(len(staged)), PAGE):
                if erasing and address % SECTOR == 0:
                    self.erase(address)
                chunk = bytes(staged[address:address + PAGE])
                if chunk and chunk.count(0xFF) != len(chunk):
                    self.program(address, chunk)

    def activate(self):
        self.stop_cpu()
        self.unprotect()
        self.program(MARKER_OFFSET, ACTIVATE_BYTE, marker=True)
        self.restart()


def swire_frame(address, data):
    """pvvx v0.5 sws_wr_addr(): ten UART bytes per SWire byte, then a stop word."""
    if not 0 <= address <= 0xFFFFFF or not data:
        raise RecoveryError('invalid SWire address or payload')
    message = bytes((0x5A, (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF, 0)) + bytes(data)
    encoded = bytearray()
    for index, value in enumerate(message):
        encoded.append(0x80 if index == 0 else 0xFE)
        for bit in range(7, -1, -1):
            encoded.append(0x80 if value & (1 << bit) else 0xFE)
        encoded.append(0xFE)
    encoded += b'\x80' * 9 + b'\xFE'
    return bytes(encoded)


class _Sink:
    def write(self, buf):
        return len(buf)

    def flush(self):
        pass


def estimate_seconds(image, passes=PASSES):
    ticks = iter(range(0, 10 ** 9, 10 ** 6))
    writer = TxOnlyWriter(_Sink(), sleep=lambda _: None, clock=lambda: next(ticks))
    writer.stage(image, passes)
    writer.activate()
    stops = writer.cpu_stops * STOP_CPU_SECONDS
    return round(writer.uart_bytes * 10 / UART_BAUD + writer.waited + stops)


def prepare(ota_path, out, *, manifest=None, role_matrix=None, expect_ota_sha256=None,
            expect_build=None):
    ota = Path(ota_path).read_bytes()
    ota_sha = sha256(ota)
    if expect_ota_sha256 and ota_sha != expect_ota_sha256.lower():
        raise RecoveryError(f'OTA SHA-256 is {ota_sha}, expected {expect_ota_sha256}')
    image, info = image_from_ota(ota)
    source = {'otaFile': Path(ota_path).name, 'otaBytes': len(ota), 'otaSha256': ota_sha}
    if manifest:
        source['manifest'] = check_manifest(read_json(manifest), ota, info)
    if role_matrix:
        source['roleMatrix'] = check_role_matrix(read_json(role_matrix), ota_sha)
    commits = {part['sourceCommit'] for part in (source.get('manifest'), source.get('roleMatrix'))
               if part and part.get('sourceCommit')}
    if len(commits) > 1:
        raise RecoveryError('manifest and role matrix come from different commits')
    if expect_build:
        builds = {part.get(key) for part, key in ((source.get('manifest'), 'swBuildId'),
                                                  (source.get('roleMatrix'), 'build')) if part}
        if not builds:
            raise RecoveryError('--expect-build needs --manifest or --role-matrix')
        if builds != {expect_build}:
            raise RecoveryError(f'build is {sorted(map(str, builds))}, expected {expect_build}')
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise RecoveryError(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    end = erase_end(len(image))
    plan = {
        'schema': SCHEMA,
        'createdUtc': utc_now().isoformat(timespec='seconds'),
        'source': source,
        'image': info,
        'slotA': {'eraseFrom': '0x00000', 'eraseTo': f'{end:#07x}', 'sectors': end // SECTOR,
                  'markerOffset': '0x08'},
        'neverWritten': f'{end:#07x}-0x7ffff',
        'transport': {'baud': UART_BAUD, 'chipFeedback': False, 'passes': PASSES},
        'estimatedSeconds': estimate_seconds(image),
    }
    (out / BUNDLE_IMAGE).write_bytes(image)
    (out / BUNDLE_PLAN).write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    return plan


def load_bundle(folder):
    folder = Path(folder)
    plan = read_json(folder / BUNDLE_PLAN)
    if plan.get('schema') != SCHEMA:
        raise RecoveryError('unsupported bundle schema')
    image = (folder / BUNDLE_IMAGE).read_bytes()
    if check_image(image) != plan.get('image'):
        raise RecoveryError('image.bin does not match plan.json')
    return plan, image


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def receipts(folder):
    path = Path(folder) / BUNDLE_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def record(folder, action, port, image_sha, writer, result, passes, now=utc_now):
    entry = {
        'utc': now().isoformat(timespec='seconds'),
        'action': action, 'port': port, 'imageSha256': image_sha, 'result': result,
        'passes': passes if action == 'stage' else None,
        'uartBytes': writer.uart_bytes, 'swireFrames': writer.frames,
        'sectorsErased': len(writer.erased), 'pagesProgrammed': writer.pages,
    }
    with (Path(folder) / BUNDLE_LOG).open('a', encoding='utf-8') as log:
        log.write(json.dumps(entry) + '\n')
    return entry


def fresh_stage(folder, image_sha, port, now=utc_now):
    """True if the latest stage of this image finished, on this port, recently.

    The kit can be reused for several devices, so an old stage receipt says
    nothing about the chip on the wires now.
    """
    for entry in reversed(receipts(folder)):
        if entry.get('action') != 'stage' or entry.get('imageSha256') != image_sha:
            continue
        if entry.get('result') != 'SENT_UNVERIFIED':
            return False
        age = (now() - datetime.datetime.fromisoformat(entry['utc'])).total_seconds()
        return entry.get('port') == port and 0 <= age <= STAGE_FRESH_SECONDS
    return False


def describe(plan):
    image, source = plan['image'], plan['source']
    manifest, matrix = source.get('manifest') or {}, source.get('roleMatrix') or {}
    name = manifest.get('swBuildId') or matrix.get('build') or source['otaFile']
    role = matrix.get('role') or '?'
    board = manifest.get('board') or '?'
    return (f'Image   {name}, role {role}, board {board}\n'
            f'        {image["bytes"]:,} bytes, SHA-256 {image["sha256"]}\n'
            f'        file version {image["fileVersion"]:#010x}, image type {image["imageType"]}, '
            f'manufacturer {image["manufacturerCode"]:#06x}\n'
            f'Writes  {plan["slotA"]["eraseFrom"]}-{plan["slotA"]["eraseTo"]} only; '
            f'never {plan["neverWritten"]}\n'
            f'Time    about {plan["estimatedSeconds"]} s for flash')


def confirm(action, image_sha, provided):
    token = f'{action.upper()}-{image_sha[:8]}'
    if provided == token:
        return
    if provided is not None:
        raise RecoveryError(f'--confirm must be {token}')
    if not sys.stdin.isatty():
        raise RecoveryError(f'{action} needs --confirm {token} when not run interactively')
    if input(f'Type {token} to {action}: ').strip() != token:
        raise RecoveryError('cancelled')


def open_port(port, timeout=0.5):
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port, re.IGNORECASE):
        raise RecoveryError('port must look like COM5 or /dev/ttyUSB0')
    try:
        import serial
    except ImportError as error:
        raise RecoveryError('pyserial is required: python -m pip install pyserial') from error
    com = serial.Serial(port=port, baudrate=UART_BAUD, timeout=timeout, write_timeout=30,
                        rtscts=False, dsrdtr=False, xonxoff=False)
    com.rts = False
    com.dtr = False
    return com


def selftest(com, seconds=3.0, chunk=512):
    """Loop TXD back to RXD on the adapter alone and compare what comes back.

    Sends in small chunks and reads each back, so the driver's receive
    buffer never has to hold more than one chunk.
    """
    pattern = swire_frame(0x0602, b'\x05') * 40 + bytes(range(256)) * 4
    com.reset_input_buffer()
    echoed = b''
    deadline = time.monotonic() + seconds
    for start in range(0, len(pattern), chunk):
        piece = pattern[start:start + chunk]
        com.write(piece)
        com.flush()
        while len(echoed) < start + len(piece) and time.monotonic() < deadline:
            echoed += com.read(start + len(piece) - len(echoed))
    return echoed == pattern, len(echoed), len(pattern)


def run_write(action, folder, port, provided, passes=PASSES, *, opener=open_port, out=print,
              now=utc_now):
    plan, image = load_bundle(folder)
    image_sha = plan['image']['sha256']
    if action == 'activate' and not fresh_stage(folder, image_sha, port, now):
        raise RecoveryError(f'activate needs a finished stage of this image on {port} within the last '
                            f'{STAGE_FRESH_SECONDS // 60} minutes; run flash instead')
    out(describe(plan))
    out('Check before you continue:')
    for line in CHECKLIST:
        out(f'  - {line}')
    out('The chip does not answer. The only proof is the device starting afterwards.')
    confirm(action, image_sha, provided)
    if passes not in SCHEDULES:
        raise RecoveryError(f'passes must be one of {sorted(SCHEDULES)}')
    entries = []
    phases = {'stage': ('stage',), 'activate': ('activate',), 'flash': ('stage', 'activate')}[action]
    with opener(port) as com:
        writer = TxOnlyWriter(com)
        for phase in phases:
            # Written before sending, so an interrupted write still leaves a trace.
            record(folder, phase, port, image_sha, writer, 'STARTED', passes, now)
            try:
                if phase == 'stage':
                    writer.stage(image, passes)
                else:
                    writer.activate()
            except BaseException as error:
                record(folder, phase, port, image_sha, writer,
                       f'INTERRUPTED: {type(error).__name__}', passes, now)
                raise
            entries.append(record(folder, phase, port, image_sha, writer, 'SENT_UNVERIFIED', passes, now))
    out(f'{action}: all bytes sent ({writer.uart_bytes:,} UART bytes). Not verified on the chip.')
    if action == 'stage':
        out('Slot A start marker is still off. Run activate next.')
    else:
        out('Chip restarted. Look for LED activity and a button response; then join checks.')
    return entries


def list_ports(out=print):
    try:
        from serial.tools import list_ports as ports
    except ImportError as error:
        raise RecoveryError('pyserial is required: python -m pip install pyserial') from error
    found = sorted(ports.comports(), key=lambda p: p.device)
    for port in found:
        out(f'{port.device}\t{port.description}\t{port.hwid}')
    if not found:
        out('No serial ports found.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('ports', help='list serial ports')
    test = commands.add_parser('selftest', help='adapter loopback test, TXD jumpered to RXD, no device')
    test.add_argument('--port', required=True)
    prep = commands.add_parser('prepare', help='make a recovery bundle from a Zigbee OTA file')
    prep.add_argument('--ota', required=True, type=Path, help='forward.ota from a CI artifact, or a stock OTA')
    prep.add_argument('--out', required=True, type=Path, help='new or empty bundle folder')
    prep.add_argument('--manifest', type=Path, help='manifest.json next to the OTA file')
    prep.add_argument('--role-matrix', type=Path, help='ROLE_MATRIX.json from the same CI artifact')
    prep.add_argument('--expect-ota-sha256')
    prep.add_argument('--expect-build', help='for example 1.2.5-bseedr22')
    show = commands.add_parser('plan', help='check a bundle and show what it would write')
    show.add_argument('--bundle', required=True, type=Path)
    for name, text in (('flash', 'stage and activate in one run'),
                       ('stage', 'write slot A with the start marker off'),
                       ('activate', 'switch the start marker on and restart')):
        write = commands.add_parser(name, help=text)
        write.add_argument('--bundle', required=True, type=Path)
        write.add_argument('--port', required=True)
        write.add_argument('--confirm', help='ACTION-<first 8 hex digits of the image SHA-256>')
        if name != 'activate':
            write.add_argument('--passes', type=int, default=PASSES,
                               help='3 (default) survives one lost erase or page; 1 and 2 do not')
    args = parser.parse_args(argv)
    try:
        if args.action == 'ports':
            list_ports()
        elif args.action == 'selftest':
            with open_port(args.port, timeout=0.2) as com:
                passed, got, sent = selftest(com)
            print(f'selftest {"PASS" if passed else "FAIL"}: {got} of {sent} bytes came back intact '
                  f'at {UART_BAUD} baud')
            print('This checks the port, driver and baud rate only. Measure the TXD level separately.')
            return 0 if passed else 1
        elif args.action == 'prepare':
            plan = prepare(args.ota, args.out, manifest=args.manifest, role_matrix=args.role_matrix,
                           expect_ota_sha256=args.expect_ota_sha256, expect_build=args.expect_build)
            print(describe(plan))
            print(f'Bundle  {args.out}')
        elif args.action == 'plan':
            plan, _ = load_bundle(args.bundle)
            print(describe(plan))
            for entry in receipts(args.bundle):
                print(f'Sent    {entry["utc"]} {entry["action"]} on {entry["port"]} ({entry["result"]})')
        else:
            run_write(args.action, args.bundle, args.port, args.confirm, getattr(args, 'passes', PASSES))
    except (RecoveryError, OSError, ValueError, KeyError) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('BLOCKED: interrupted. Slot A may be partly erased; run flash again.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
