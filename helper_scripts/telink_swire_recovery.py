#!/usr/bin/env python3
"""Wired recovery of Telink TLSR8258 Zigbee devices with a USB-UART adapter.

Writes a firmware image into slot A (0x00000) or B (0x40000) of a 512 KiB TLSR8258
without a bootloader. Only the adapter's TXD drives the chip's SWS pin
(pvvx's TX-only SWire method), so the chip never answers: this tool cannot
confirm that flash changed. Success is the device starting afterwards.

Typical use
  wiring --device gledopto-gl-sd-301p          how to connect, step by step
  selftest --port COM5                         adapter check, TXD jumpered to RXD
  prepare --device ... --out kit               make a bundle (offline, or the pinned download)
  flash --bundle kit --port COM5               write it

Commands
  ports      list serial ports
  wiring     pads, checks and notes for a device (all devices without --device)
  selftest   loopback test of the adapter alone (TXD jumpered to RXD)
  prepare    make a recovery bundle from a Zigbee OTA file or a finalized image
  plan       check a bundle and show what it writes and what was sent before
  flash      stage, then activate, in one run
  stage      erase the selected slot and write with its boot marker off
  inspect    identify both slots from a matching full double read
  activate   switch the boot marker on (one byte, no erase) and restart

flash, stage and activate take --dry-run FILE to save the exact byte stream
and an operation trace instead of opening a port. Every command takes --json
for one machine-readable result object on stdout.

Never written: the other slot, either NVM area, IEEE address or factory data.
Default target is A; B requires a verified double-read baseline with A disabled. Needs pyserial for the commands
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
import subprocess
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
POWER_CYCLE_SECONDS = 10.0        # time to switch 3.3 V on while the halt is sent
STAGE_FRESH_SECONDS = 1800
PROGRESS_STEP = 0x8000

SECTOR = 0x1000
PAGE = 0x100
SLOT_A_END = 0x34000              # size of either application slot; never includes NVM
SLOTS = {'A': 0, 'B': 0x40000}
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
SCHEMA = 2
CHECKLIST = (
    'Board off mains; nothing mains-connected touches the board or the PC.',
    'Adapter in TTL mode; TXD idles at about 3.3 V (measured, not assumed).',
    'TXD -> SWS, GND -> GND, 3V3 -> 3V3 (or a separate 3.3 V supply). RXD unconnected. Never +5V.',
    'Every lead proven by a continuity test; clips hold on their own; nothing bridges neighbouring pads.',
)
GUIDE = 'docs/flashing/swire_recovery.md'

GLEDOPTO_STOCK_COMMIT = '895567036301d25da68161358f96a37971488a08'
DEVICES = {
    'bseed-ts011f': {
        'name': 'BSEED TS011F-BS / TS011F-BS-PM socket (Tuya ZTU module)',
        'manufacturerCodes': [0x1141],
        'imageTypes': [43555, 43556, 65024, 65026],
        'boards': ['OUTLET_BSEED'],
        'requiresEvidence': True,
        'pads': [
            ('SWS', 'castellated pad on the ZTU module edge, silkscreen SWS (module pin 4)'),
            ('GND', 'module pin 13, or a carrier-board GND pad that measures 0 ohm to it'),
            ('3V3', 'module pin 14 (VCC), or the carrier-board 3V3 pad'),
        ],
        'notes': [
            'Take the board out of the socket housing first. Never work on a socket that is plugged in.',
            'The adapter 3V3 normally powers the module. The relay may not switch on 3.3 V; that is fine.',
            'The Zigbee network record is kept, so the socket rejoins without pairing.',
            'Use a kit from the BSEED PM or non-PM role matrix run; prepare refuses images without that CI evidence.',
        ],
    },
    'gledopto-gl-sd-301p': {
        'name': 'Gledopto GL-SD-301P dimmer (TLSR8258F512ET32)',
        'manufacturerCodes': [0x124F],
        'imageTypes': [0x1416],
        'images': {
            '7ba122f1ebc9f6ec419d9ea6b27676d55c2a2aad864a03e90b896546248c4b48':
                'Gledopto stock V20851203 (20251230)',
        },
        'buildEvidence': 'gledopto',
        'stockOta': {
            'file': 'GL-SD-301P_V20851203_20251230.ota',
            'url': ('https://raw.githubusercontent.com/analienx/gledopto/' + GLEDOPTO_STOCK_COMMIT
                    + '/devices/gl-sd-301p/vendor-firmware/originals/GL-SD-301P_V20851203_20251230.ota'),
            'bytes': 208946,
            'sha256': '16595a38ab9783d3afc4eb58ab4ec32625249bd569c1fa6dbaf468bddc76dd72',
        },
        'pads': [
            ('SWS', 'trace it to the TLSR8258 PA7/SWS pin; candidates are the small pads near the crystal'),
            ('GND', 'a pad that measures 0 ohm to the TLSR8258 ground pad'),
            ('3V3', 'the module 3.3 V rail, measured, not the mains power stage'),
        ],
        'notes': [
            'The GND | 3.3V | SWS order seen in photos is a visual guess. Prove every pad by continuity.',
            'Mini-hooks do not hold on flat pads. Solder thin temporary wires or use a pogo-pin fixture.',
            'If the 3.3 V rail sags below 3.0 V under load, use a separate current-limited 3.3 V supply.',
            'Only the pinned vendor stock image is admitted by this recovery profile. Custom builds remain quarantined.',
            'Preserving flash does not prove the retained network record is compatible with stock firmware.',
        ],
    },
    'generic-tlsr8258': {
        'name': 'Other TLSR8258 512 KiB device (no profile)',
        'requiresInputSha256': True,
        'pads': [('SWS', 'the TLSR8258 SWS/PA7 pin'), ('GND', 'ground'), ('3V3', 'the 3.3 V rail')],
        'notes': ['No device-specific checks. The flash layout must match the 512 KiB SDK layout.'],
    },
}


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


def check_gledopto_build(folder, image_name, image, info):
    """SHA256SUMS and manifest.txt of an analienx/gledopto wired-image CI artifact."""
    sums_path, manifest_path = folder / 'SHA256SUMS', folder / 'manifest.txt'
    if not sums_path.is_file() or not manifest_path.is_file():
        raise RecoveryError('a Gledopto image needs SHA256SUMS and manifest.txt from the same CI '
                            'artifact next to it')
    sums = {}
    for line in sums_path.read_text(encoding='utf-8').splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip('*')] = parts[0].lower()
    if sums.get(image_name) != info['sha256']:
        raise RecoveryError(f'SHA256SUMS does not list {image_name} with this SHA-256')
    fields, digests = {}, {}
    for line in manifest_path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([A-Z0-9_a-z.-]+)=(.*)', line.strip())
        if match:
            fields[match.group(1)] = match.group(2)
        match = re.fullmatch(r'([0-9a-f]{64})\s+\S*\.final\.bin', line.strip())
        if match:
            digests[match.group(1)] = True
    if fields.get('FINAL_BINARY_SIZE') != str(len(image)):
        raise RecoveryError('manifest.txt FINAL_BINARY_SIZE differs from the image')
    if info['sha256'] not in digests:
        raise RecoveryError('manifest.txt does not record this image as its final.bin')
    identity = fields.get('DEV_IDENTITY', '')
    version = re.search(r'FILE_VERSION_0x([0-9A-Fa-f]{8})', identity)
    if not version or int(version.group(1), 16) != info['fileVersion']:
        raise RecoveryError('manifest.txt DEV_IDENTITY file version differs from the image')
    keep = ('DEV_IDENTITY', 'QUARANTINED_BUILD', 'DEPLOYABLE', 'FIRST_FLASHABLE_CANARY_ALLOWED',
            'ZB_ED_ROLE', 'ZB_ROUTER_ROLE', 'STACK_NV')
    return {key: fields[key] for key in keep if key in fields}


def check_device(device_id, info, source):
    """Make sure the image belongs to the device profile and carries the evidence it needs."""
    device = DEVICES.get(device_id)
    if device is None:
        raise RecoveryError(f'unknown device {device_id!r}; use one of {", ".join(DEVICES)}')
    codes = device.get('manufacturerCodes')
    if codes and info['manufacturerCode'] not in codes:
        raise RecoveryError(f'image is for manufacturer {info["manufacturerCode"]:#06x}, '
                            f'not a {device["name"]}')
    types = device.get('imageTypes')
    if types and info['imageType'] not in types:
        raise RecoveryError(f'image type {info["imageType"]:#06x} is not a {device["name"]} image')
    boards = device.get('boards')
    manifest = source.get('manifest') or {}
    if boards and manifest and not any(str(manifest.get('board')).startswith(b) for b in boards):
        raise RecoveryError(f'manifest board {manifest.get("board")} is not a {device["name"]}')
    if device.get('requiresEvidence') and not (source.get('manifest') and source.get('roleMatrix')):
        raise RecoveryError(f'{device_id} needs --manifest and --role-matrix from the same CI run; '
                            'download a wired kit instead')
    images = device.get('images')
    if device_id == 'gledopto-gl-sd-301p' and info['sha256'] not in images:
        raise RecoveryError('Gledopto custom firmware remains quarantined; this profile permits stock recovery only')
    if images is not None and info['sha256'] not in images and not source.get('buildManifest'):
        raise RecoveryError(f'image {info["sha256"][:12]} is not a known {device_id} image and has no '
                            'CI build evidence (SHA256SUMS and manifest.txt next to it)')
    if device.get('requiresInputSha256') and not source.get('pinnedSha256'):
        raise RecoveryError(f'{device_id} needs --expect-sha256 of the input file')
    return device


def image_label(device_id, info, source):
    images = DEVICES[device_id].get('images') or {}
    manifest, matrix = source.get('manifest') or {}, source.get('roleMatrix') or {}
    build = (source.get('buildManifest') or {}).get('DEV_IDENTITY')
    return (images.get(info['sha256']) or manifest.get('swBuildId') or matrix.get('build') or build
            or source.get('inputFile'))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def download(url, limit):
    if not url.startswith('https://raw.githubusercontent.com/'):
        raise RecoveryError(f'refusing to download from {url}')
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read(limit + 1)


class TxOnlyWriter:
    """Send-only SWire commands for the TLSR8258 flash controller. Never reads.

    power_on: in power-cycle mode, called when the halt starts; the person
    switches 3.3 V on then, and no restart is sent over SWS. progress is
    called as stage moves through the image. trace, when a list, receives
    every operation; last always holds the latest one, for receipts.
    """

    def __init__(self, port, *, sleep=time.sleep, clock=time.monotonic, power_on=None,
                 progress=None, trace=None, slot='A', full_slot=False, hold=False):
        if slot not in SLOTS:
            raise RecoveryError('slot must be A or B')
        self.slot = slot
        self.base = SLOTS[slot]
        self.full_slot = full_slot
        self.hold = hold
        self.baseline_sha = None
        self.port = port
        self.sleep = sleep
        self.clock = clock
        self.power_on = power_on
        self.progress = progress
        self.trace = trace
        self.halted = False
        self.uart_bytes = 0
        self.frames = 0
        self.waited = 0.0
        self.erased = []
        self.pages = 0
        self.cpu_stops = 0
        self.pass_no = None
        self.last = None

    def elapsed(self):
        """Time the bytes and waits so far take on the wire."""
        return self.uart_bytes * 10 / UART_BAUD + self.waited

    def note(self, operation, **fields):
        self.last = {'operation': operation, 'pass': self.pass_no, **fields}
        if self.trace is not None:
            self.trace.append(dict(self.last, wireSeconds=round(self.elapsed(), 3)))

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
        self.note('restart')
        self.sws(0x006F, b'\x20')

    def stop_cpu(self):
        """Halt the CPU while its ROM starts, so it never runs the firmware.

        Normally a restart over SWS starts the ROM. In power-cycle mode the
        person switches 3.3 V on instead, which also works when the running
        firmware has turned SWS off. A chip this writer already halted stays
        halted, so a later call only repeats the halt.
        """
        stop = swire_frame(0x0602, b'\x05')
        if self.halted:
            self.raw(stop)
            return
        seconds = STOP_CPU_SECONDS
        if self.power_on is not None and not self.halted:
            self.note('stop-cpu', powerCycle=True)
            self.power_on()
            seconds = POWER_CYCLE_SECONDS
        elif self.power_on is None:
            self.restart()
            self.note('stop-cpu', powerCycle=False)
        else:
            self.note('stop-cpu', powerCycle=True, alreadyHalted=True)
        start = self.clock()
        while self.clock() - start < seconds:
            self.raw(stop)
        self.sws(0x00B2, bytes((SWS_DIVIDER,)))
        self.raw(stop)
        self.command(0xAB)                            # release flash from power-down
        self.cpu_stops += 1
        self.halted = True

    def unprotect(self):
        # pvvx v0.5 writes status 0 twice. Clears any block protection.
        self.note('unprotect')
        for _ in range(2):
            self.command(0x06)
            self.sws(0x0D, b'\x00')
            self.sws(0x0C, b'\x01')
            self.sws(0x0C, b'\x00\x01')
            self.wait(STATUS_WAIT)

    def erase(self, address):
        if address % SECTOR or not self.base <= address < self.base + SLOT_A_END:
            raise RecoveryError(f'refusing to erase {address:#x}: outside slot {self.slot}')
        self.note('erase', address=f'{address:#07x}')
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
            if (address, data) != (self.base + MARKER_OFFSET, ACTIVATE_BYTE):
                raise RecoveryError('the marker write can only enable the selected slot')
        elif (not data or address < self.base or address + len(data) > self.base + SLOT_A_END
              or address // PAGE != (address + len(data) - 1) // PAGE):
            raise RecoveryError(f'refusing to program {len(data)} bytes at {address:#x}')
        elif (address <= self.base + MARKER_OFFSET < address + len(data)
              and data[self.base + MARKER_OFFSET - address] != 0xFF):
            raise RecoveryError('stage must leave the start marker off')
        self.note('program', address=f'{address:#07x}', bytes=len(data), marker=marker)
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
        """Erase only the selected slot and write with the start marker left at 0xFF.

        See SCHEDULES for which passes erase. The erase always reaches
        MIN_ERASE_END so no stale start marker survives at 0x8008 or 0x20008.
        """
        if passes not in SCHEDULES:
            raise RecoveryError(f'passes must be one of {sorted(SCHEDULES)}')
        check_image(image)
        staged = bytearray(image)
        staged[MARKER_OFFSET] = 0xFF
        end = SLOT_A_END if self.full_slot else erase_end(len(staged))
        self.stop_cpu()
        self.unprotect()
        for number, erasing in enumerate(SCHEDULES[passes], 1):
            self.pass_no = number
            for address in range(0, end, PAGE):
                if self.progress and address % PROGRESS_STEP == 0:
                    self.progress(number, passes, address, end)
                if erasing and address % SECTOR == 0:
                    self.erase(self.base + address)
                chunk = bytes(staged[address:address + PAGE])
                if chunk and chunk.count(0xFF) != len(chunk):
                    self.program(self.base + address, chunk)
        self.pass_no = None

    def activate(self):
        self.stop_cpu()
        self.unprotect()
        self.program(self.base + MARKER_OFFSET, ACTIVATE_BYTE, marker=True)
        if not self.hold:
            self.restart()
            self.halted = False


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


class _FileSink:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def write(self, buf):
        self.stream.write(buf)
        self.digest.update(buf)
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


def load_input(device_id, ota_path, image_path, expect_sha256, fetch):
    """Read the OTA file or finalized image and return (image, info, source)."""
    device = DEVICES.get(device_id)
    if device is None:
        raise RecoveryError(f'unknown device {device_id!r}; use one of {", ".join(DEVICES)}')
    if ota_path and image_path:
        raise RecoveryError('give --ota or --image, not both')
    source = {}
    if not ota_path and not image_path:
        stock = device.get('stockOta')
        if not stock:
            raise RecoveryError(f'{device_id} needs --ota or --image')
        data = fetch(stock['url'], stock['bytes'])
        if sha256(data) != stock['sha256'] or len(data) != stock['bytes']:
            raise RecoveryError(f'downloaded {stock["file"]} does not match its pinned SHA-256')
        source.update(inputFile=stock['file'], inputUrl=stock['url'], pinnedSha256=stock['sha256'])
        kind, path = 'ota', None
    else:
        kind, path = ('ota', Path(ota_path)) if ota_path else ('image', Path(image_path))
        data = path.read_bytes()
        source['inputFile'] = path.name
    digest = sha256(data)
    if expect_sha256:
        if digest != expect_sha256.lower():
            raise RecoveryError(f'{source["inputFile"]} SHA-256 is {digest}, expected {expect_sha256}')
        source['pinnedSha256'] = digest
    source.update(inputKind=kind, inputBytes=len(data), inputSha256=digest)
    if kind == 'ota':
        image, info = image_from_ota(data)
    else:
        image, info = data, check_image(data)
        if device.get('buildEvidence') == 'gledopto' and info['sha256'] not in device.get('images', {}):
            source['buildManifest'] = check_gledopto_build(path.parent, path.name, image, info)
        elif device.get('requiresEvidence'):
            raise RecoveryError(f'{device_id} images come as forward.ota with their CI evidence, '
                                'not as a bare image')
    return image, info, source, data


def prepare(ota_path=None, out=None, *, device='bseed-ts011f', image_path=None, manifest=None, role_matrix=None,
            expect_sha256=None, expect_build=None, fetch=download, expect_ota_sha256=None):
    expect_sha256 = expect_sha256 or expect_ota_sha256
    image, info, source, data = load_input(device, ota_path, image_path, expect_sha256, fetch)
    if manifest:
        source['manifest'] = check_manifest(read_json(manifest), data, info)
    if role_matrix:
        source['roleMatrix'] = check_role_matrix(read_json(role_matrix), source['inputSha256'])
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
    check_device(device, info, source)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise RecoveryError(f'{out} is not empty')
    end = erase_end(len(image))
    plan = {
        'schema': SCHEMA,
        'createdUtc': utc_now().isoformat(timespec='seconds'),
        'device': {'id': device, 'name': DEVICES[device]['name']},
        'label': image_label(device, info, source),
        'source': source,
        'image': info,
        'slotA': {'eraseFrom': '0x00000', 'eraseTo': f'{end:#07x}', 'sectors': end // SECTOR,
                  'markerOffset': '0x08'},
        'neverWritten': f'{end:#07x}-0x7ffff',
        'transport': {'baud': UART_BAUD, 'chipFeedback': False, 'passes': PASSES},
        'estimatedSeconds': estimate_seconds(image),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / BUNDLE_IMAGE).write_bytes(image)
    (out / BUNDLE_PLAN).write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    return plan


def load_bundle(folder):
    folder = Path(folder)
    plan = read_json(folder / BUNDLE_PLAN)
    if plan.get('schema') != SCHEMA:
        raise RecoveryError('bundle was made by another version of this tool; make it again with '
                            'prepare, or download a new kit')
    image = (folder / BUNDLE_IMAGE).read_bytes()
    info = check_image(image)
    if info != plan.get('image'):
        raise RecoveryError('image.bin does not match plan.json')
    check_device(plan.get('device', {}).get('id'), info, plan.get('source', {}))
    return plan, image


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def receipts(folder):
    path = Path(folder) / BUNDLE_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def record(folder, action, port, image_sha, writer, result, passes, now=utc_now, power_cycle=False):
    entry = {
        'utc': now().isoformat(timespec='seconds'),
        'action': action, 'port': port, 'imageSha256': image_sha, 'result': result,
        'passes': passes if action == 'stage' else None, 'powerCycle': power_cycle,
        'uartBytes': writer.uart_bytes, 'swireFrames': writer.frames,
        'sectorsErased': len(writer.erased), 'pagesProgrammed': writer.pages,
        'lastOperation': writer.last,
        'slot': writer.slot, 'fullSlot': writer.full_slot, 'hold': writer.hold,
        'baselineSha256': writer.baseline_sha,
    }
    with (Path(folder) / BUNDLE_LOG).open('a', encoding='utf-8') as log:
        log.write(json.dumps(entry) + '\n')
    return entry


def fresh_stage(folder, image_sha, port, now=utc_now, *, slot='A', full_slot=False, baseline_sha=None):
    """True if the latest stage of this image finished, on this port, recently.

    The kit can be reused for several devices, so an old stage receipt says
    nothing about the chip on the wires now.
    """
    for entry in reversed(receipts(folder)):
        if entry.get('action') != 'stage' or entry.get('imageSha256') != image_sha:
            continue
        if entry.get('slot', 'A') != slot or entry.get('fullSlot', False) != full_slot:
            return False
        if entry.get('baselineSha256') != baseline_sha:
            return False
        if entry.get('result') != 'SENT_UNVERIFIED':
            return False
        age = (now() - datetime.datetime.fromisoformat(entry['utc'])).total_seconds()
        return entry.get('port') == port and 0 <= age <= STAGE_FRESH_SECONDS
    return False


def describe(plan, *, slot='A', full_slot=False):
    image, source = plan['image'], plan['source']
    matrix = source.get('roleMatrix') or {}
    board = (source.get('manifest') or {}).get('board')
    extra = ''.join(f', {k} {v}' for k, v in (('role', matrix.get('role')), ('board', board)) if v)
    lines = [f'Device  {plan["device"]["name"]}',
             f'Image   {plan["label"]}{extra}',
             f'        {image["bytes"]:,} bytes, SHA-256 {image["sha256"]}',
             f'        file version {image["fileVersion"]:#010x}, image type {image["imageType"]:#06x}, '
             f'manufacturer {image["manufacturerCode"]:#06x}']
    build = source.get('buildManifest')
    if build:
        flags = ', '.join(f'{k}={build[k]}' for k in ('QUARANTINED_BUILD', 'DEPLOYABLE',
                                                       'FIRST_FLASHABLE_CANARY_ALLOWED') if k in build)
        lines.append(f'Build   {build.get("DEV_IDENTITY", "?")}; {flags}')
    start = SLOTS[slot]
    end = start + (SLOT_A_END if full_slot else erase_end(image['bytes']))
    lines += [f'Writes  slot {slot}: {start:#07x}-{end:#07x} only (end exclusive); all other flash kept',
              f'Time    about {plan["estimatedSeconds"]} s for flash']
    return '\n'.join(lines)


def wiring(device_id):
    device = DEVICES.get(device_id)
    if device is None:
        raise RecoveryError(f'unknown device {device_id!r}; use one of {", ".join(DEVICES)}')
    lines = [device['name'], '',
             'Connect (board off mains, everything unpowered while you measure):']
    lines += [f'  {"TXD" if pad == "SWS" else pad:<4} -> {pad:<4} {where}' for pad, where in device['pads']]
    lines += ['  RXD, RTS, DTR and +5V stay unconnected.', '',
              'Before you write:',
              '  1. Continuity: each lead to its pad, and no continuity between SWS, GND and 3V3.',
              '  2. Adapter in 3.3 V TTL mode. TXD idles at about 3.3 V against GND.',
              '  3. selftest --port COMx passes with TXD jumpered to RXD, the device not connected.',
              '  4. The 3.3 V rail reads 3.2-3.4 V with the board connected.', '',
              'Notes:']
    lines += [f'  - {note}' for note in device['notes']]
    lines += ['', 'If the device does not start after flash: preserve evidence, check the leads and 3.3 V, '
              'then assess the incident fallback before another write.', f'Troubleshooting: {GUIDE}']
    return '\n'.join(lines)


def ask(prompt):
    print(prompt, end='', file=sys.stderr, flush=True)
    return sys.stdin.readline()


def confirm(action, image_sha, provided, interactive):
    token = f'{action.upper()}-{image_sha[:8]}'
    if provided == token:
        return
    if provided is not None:
        raise RecoveryError(f'--confirm must be {token}')
    if not interactive:
        raise RecoveryError(f'{action} needs --confirm {token} when not run interactively')
    if ask(f'Type {token} to {action}: ').strip() != token:
        raise RecoveryError('cancelled')


def open_port(port, timeout=0.5):
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
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


PHASES = {'stage': ('stage',), 'activate': ('activate',), 'flash': ('stage', 'activate')}


def run_phase(writer, phase, image, passes):
    if phase == 'stage':
        writer.stage(image, passes)
    else:
        writer.activate()


def dry_run(action, plan, image, passes, power_cycle, path, out, *, slot='A', full_slot=False, hold=False):
    """Write the exact UART stream and an operation trace to files instead of a port."""
    path = Path(path)
    trace_path = path.with_name(path.name + '.trace.jsonl')
    if path.exists() or trace_path.exists():
        raise RecoveryError(f'{path} or its trace already exists; choose a new file name')
    trace = []
    with path.open('xb') as stream:
        sink = _FileSink(stream)
        writer = TxOnlyWriter(sink, sleep=lambda _: None, trace=trace,
                              power_on=(lambda: None) if power_cycle else None,
                              slot=slot, full_slot=full_slot, hold=hold)
        writer.clock = writer.elapsed           # the flood lasts as long as it would on the wire
        for phase in PHASES[action]:
            run_phase(writer, phase, image, passes)
    trace_path.write_text(''.join(json.dumps(entry) + '\n' for entry in trace), encoding='utf-8')
    result = {'status': 'DRY_RUN', 'action': action, 'imageSha256': plan['image']['sha256'],
              'slot': slot, 'fullSlot': full_slot, 'hold': hold,
              'passes': passes, 'powerCycle': power_cycle, 'stream': str(path),
              'streamBytes': writer.uart_bytes, 'streamSha256': sink.digest.hexdigest(),
              'swireFrames': writer.frames, 'sectorsErased': len(writer.erased),
              'pagesProgrammed': writer.pages, 'wireSeconds': round(writer.elapsed()),
              'trace': str(trace_path), 'operations': len(trace)}
    out(f'{action} dry run: {writer.uart_bytes:,} UART bytes, {len(writer.erased)} sector erases, '
        f'{writer.pages} page writes, about {result["wireSeconds"]} s on the wire.')
    out(f'Stream  {path} (SHA-256 {result["streamSha256"]})')
    out(f'Trace   {trace_path}. Nothing was sent; no receipt was written.')
    return result


def run_write(action, folder, port, provided, passes=PASSES, *, power_cycle=False, dry_run_path=None,
              opener=open_port, out=print, now=utc_now, interactive=None,
              slot='A', full_slot=False, baseline=None, hold=False):
    plan, image = load_bundle(folder)
    image_sha = plan['image']['sha256']
    if passes not in SCHEDULES:
        raise RecoveryError(f'passes must be one of {sorted(SCHEDULES)}')
    if slot not in SLOTS:
        raise RecoveryError('slot must be A or B')
    if slot == 'B' and not baseline:
        raise RecoveryError('slot B requires --baseline from a matching double read with slot A disabled')
    baseline_sha = None
    if baseline:
        before = matched_backup(baseline, port=None if dry_run_path else port)
        baseline_sha = sha256(before)
        if slot == 'B' and before[8:12] != b'\x00NLT':
            raise RecoveryError('slot A is not explicitly disabled; slot B cannot take boot priority. Stop and inspect')
        if slot == 'B':
            state = inspect_slots(baseline)
            if not state['slots'][0]['validImage'] or state['legacyMarkers']:
                raise RecoveryError('slot A has an unknown image or legacy layout marker; stop before writing B')
    if dry_run_path:
        return dry_run(action, plan, image, passes, power_cycle, dry_run_path, out,
                       slot=slot, full_slot=full_slot, hold=hold)
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if action == 'activate' and not fresh_stage(folder, image_sha, port, now, slot=slot,
                                               full_slot=full_slot, baseline_sha=baseline_sha):
        raise RecoveryError(f'activate needs a finished stage of this image on {port} within the last '
                            f'{STAGE_FRESH_SECONDS // 60} minutes; run flash instead')
    if power_cycle and not interactive:
        raise RecoveryError('--power-cycle needs a person at an interactive terminal to switch 3.3 V on '
                            'when asked')
    out(describe(plan, slot=slot, full_slot=full_slot))
    end = SLOT_A_END if full_slot else erase_end(len(image))
    out(f'Actual target: slot {slot}, {SLOTS[slot]:#07x}-{SLOTS[slot] + end:#07x} (end exclusive).')
    out('Check before you continue:')
    for line in CHECKLIST:
        out(f'  - {line}')
    out(f'  Device notes: wiring --device {plan["device"]["id"]}')
    out('The chip does not answer. The only proof is the device starting afterwards.')
    # Non-default layouts cannot reuse the default slot-A confirmation token.
    confirm(action if slot == 'A' and not full_slot else f'{action}-{slot}-full' if full_slot else f'{action}-{slot}',
            image_sha, provided, interactive)
    if power_cycle:
        out('Power-cycle mode: disconnect only the 3V3 lead (or switch the 3.3 V supply off). '
            'Keep TXD and GND connected.')
        ask('Press Enter when the board has no power: ')
    entries = []

    def power_on():
        out(f'>>> Connect 3.3 V NOW. The chip is halted as it starts; you have '
            f'{POWER_CYCLE_SECONDS:.0f} s. <<<')

    def progress(number, total, address, end):
        out(f'  pass {number} of {total}: {address:#07x} of {end:#07x} ({100 * address // end}%)')

    with opener(port) as com:
        writer = TxOnlyWriter(com, power_on=power_on if power_cycle else None, progress=progress,
                              slot=slot, full_slot=full_slot, hold=hold)
        writer.baseline_sha = baseline_sha
        for phase in PHASES[action]:
            # Written before sending, so an interrupted write still leaves a trace.
            record(folder, phase, port, image_sha, writer, 'STARTED', passes, now, power_cycle)
            out(f'{phase} started')
            try:
                run_phase(writer, phase, image, passes)
            except BaseException as error:
                record(folder, phase, port, image_sha, writer,
                       f'INTERRUPTED: {type(error).__name__}', passes, now, power_cycle)
                raise
            entries.append(record(folder, phase, port, image_sha, writer, 'SENT_UNVERIFIED', passes,
                                  now, power_cycle))
    out(f'{action}: all bytes sent ({writer.uart_bytes:,} UART bytes). Not verified on the chip.')
    if action == 'stage':
        out(f'Slot {slot} start marker was sent inactive. Verify readback before activation when available.')
    elif hold:
        out('Activation sent; no restart requested. Verify full readback before the controlled bench start.')
    else:
        out('Chip restarted. Look for LED activity and a button response; then the Zigbee checks.')
        out(f'If nothing happens: preserve evidence, check the leads and 3.3 V, then assess the incident fallback. '
            f'See {GUIDE}.')
    return entries


def list_ports():
    try:
        from serial.tools import list_ports as ports
    except ImportError as error:
        raise RecoveryError('pyserial is required: python -m pip install pyserial') from error
    return [{'device': p.device, 'description': p.description, 'hwid': p.hwid}
            for p in sorted(ports.comports(), key=lambda p: p.device)]


READER_COMMIT = '042613b1b47a1598856b0ff3e8a133f2918798a5'
READER_SHA256 = '2ea1b7c5266821e5d384a263daa7916bb4020fcdfb8f1ec0bed5842dcb94ca18'
READER_URL = ('https://raw.githubusercontent.com/pvvx/TlsrComSwireWriter/'
              + READER_COMMIT + '/TLSR825xComFlasher.py')
FLASH_BYTES = 0x80000


def private_output(path):
    path = Path(path).resolve()
    if any((parent / '.git').exists() for parent in (path.parent, *path.parents)):
        raise RecoveryError('flash dumps contain network keys; choose an output outside every Git checkout')
    return path


def read_flash(port, output, *, address=0, size=FLASH_BYTES, reader=None,
               halt=False, fetch=download, runner=subprocess.run):
    """Two independent rf invocations; never expose upstream erase/write commands.

    Requires the pvvx bidirectional circuit, NOT the TX-only hookup. Reading
    changes volatile SWire/SPI registers. --halt additionally resets/halts the
    CPU via upstream -t, which also toggles RTS/DTR; leave those pins unwired
    unless the reset circuit has been independently qualified. No CPU restart
    is requested after reading. Partial and disagreeing dumps are retained.
    """
    if not 0 <= address < address + size <= FLASH_BYTES:
        raise RecoveryError('read range must be nonempty and within 512 KiB flash')
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
        raise RecoveryError('invalid serial port')
    output = private_output(output)
    second = output.with_name(output.name + '.second.bin')
    receipt = output.with_name(output.name + '.json')
    if any(p.exists() for p in (output, second, receipt)):
        raise RecoveryError('read output or receipt already exists; choose a new output')
    output.parent.mkdir(parents=True, exist_ok=True)
    if reader is None:
        reader = output.parent / ('pvvx-reader-' + READER_COMMIT + '.py')
        if not reader.exists():
            data = fetch(READER_URL, 256000)
            if sha256(data) != READER_SHA256:
                raise RecoveryError('downloaded reader SHA-256 mismatch')
            with reader.open('xb') as stream:
                stream.write(data)
    reader = Path(reader).resolve()
    if sha256(reader.read_bytes()) != READER_SHA256:
        raise RecoveryError('reader must match the pinned pvvx source SHA-256')
    result = {'status': 'STARTED', 'address': address, 'bytes': size,
              'port': port, 'readerCommit': READER_COMMIT, 'halt': halt,
              'utc': utc_now().isoformat(), 'reads': []}
    try:
        for target in (output, second):
            command = [sys.executable, str(reader), '-p', port, '-b', '921600']
            if halt:
                command += ['-t', '3000']
            command += ['rf', hex(address), hex(size), str(target)]
            completed = runner(command, capture_output=True, text=True, timeout=1800)
            if completed.returncode != 0:
                raise RecoveryError('bidirectional read failed; check the pvvx circuit, voltage and contact. '
                                    'No TX-only fallback or write was attempted')
            data = target.read_bytes()
            if len(data) != size:
                raise RecoveryError('short flash read; retained for diagnosis')
            result['reads'].append({'file': str(target), 'sha256': sha256(data)})
        if result['reads'][0]['sha256'] != result['reads'][1]['sha256']:
            raise RecoveryError('two flash reads differ; no trustworthy backup established')
        result['status'] = 'READ_MATCHED'
        return result
    except BaseException:
        result['status'] = 'READ_FAILED'
        raise
    finally:
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)


def matched_backup(path, *, port=None):
    """Require the bytes and both files of the original successful full read."""
    path = Path(path)
    data = path.read_bytes()
    second = path.with_name(path.name + '.second.bin').read_bytes()
    receipt = read_json(path.with_name(path.name + '.json'))
    digests = [entry.get('sha256') for entry in receipt.get('reads', [])]
    if (len(data) != FLASH_BYTES or second != data or receipt.get('status') != 'READ_MATCHED'
            or receipt.get('address') != 0 or receipt.get('bytes') != FLASH_BYTES
            or digests != [sha256(data), sha256(data)]
            or (port is not None and receipt.get('port') != port)):
        raise RecoveryError('baseline must be an unchanged matching double read of full flash on this port')
    return data


def inspect_slots(dump, references=(), *, replace=None, expect_other=None):
    """Identify bytes, not execution. A marker cannot prove firmware ever ran.

    Disabled OTA images differ from the original at byte 8. Normalize that
    one known marker byte for CRC and exact-image matching; record its real
    state separately. Unknown markers, corruption and ambiguous selection
    never produce an in-place replacement recommendation.
    """
    data = matched_backup(dump)
    known = {}
    for item in references:
        label, separator, filename = item.partition('=')
        if not separator or not label or label in known:
            raise RecoveryError('--reference must be a unique NAME=FILE')
        source = Path(filename).read_bytes()
        if len(source) >= 4 and u32(source, 0) == OTA_MAGIC:
            source, info = image_from_ota(source)
        else:
            info = check_image(source)
        known[label] = info
    if replace and replace not in known:
        raise RecoveryError('--replace must name a supplied reference')
    if expect_other and (not replace or expect_other not in known or expect_other == replace):
        raise RecoveryError('--expect-other needs --replace and a different supplied reference')
    slots = []
    for name, base in SLOTS.items():
        raw = data[base:base + SLOT_A_END]
        marker = raw[8:12]
        state = {MARKER: 'enabled', b'\x00NLT': 'disabled', b'\xffNLT': 'staged'}.get(marker, 'unknown')
        entry = {'slot': name, 'address': base, 'markerHex': marker.hex(),
                 'markerState': state, 'validImage': False, 'matches': []}
        try:
            if state == 'unknown':
                raise RecoveryError('unrecognized marker')
            length = u32(raw, SIZE_OFFSET)
            if not 0x20 <= length < SLOT_A_END:
                raise RecoveryError('invalid declared length')
            image = bytearray(raw[:length])
            image[8] = ACTIVATE_BYTE[0]
            info = check_image(bytes(image))
            entry.update(validImage=True, image=info,
                         matches=[label for label, ref in known.items() if ref == info])
        except RecoveryError as error:
            entry['error'] = str(error)
        slots.append(entry)
    enabled = [s for s in slots if s['markerState'] == 'enabled']
    legacy = [offset for offset in OTHER_START_MARKERS if data[offset:offset + 4] == MARKER]
    result = {'status': 'INSPECTED', 'dumpSha256': sha256(data), 'slots': slots, 'legacyMarkers': legacy,
              'bootCandidate': enabled[0]['slot'] if len(enabled) == 1 and not legacy else None,
              'replaceSlot': None, 'bootVerified': False}
    if replace:
        if legacy:
            raise RecoveryError('legacy boot-layout marker present; stop before selecting a replacement slot')
        if len(enabled) != 1 or not enabled[0]['validImage'] or replace not in enabled[0]['matches']:
            raise RecoveryError('no unique enabled slot matching the replacement reference; stop before writing')
        other = next(s for s in slots if s is not enabled[0])
        if other['markerState'] != 'disabled' or not other['validImage']:
            raise RecoveryError('other slot is not a valid explicitly disabled image; stop before writing')
        if expect_other and expect_other not in other['matches']:
            raise RecoveryError('other slot does not match --expect-other; stop before writing')
        result['replaceSlot'] = enabled[0]['slot']
    return result


def verify_dump(folder, dump, *, staged=False, baseline=None, slot='A', full_slot=False):
    """Offline comparison of a full readback; no device is opened or restarted."""
    plan, image = load_bundle(folder)
    data = Path(dump).read_bytes()
    if len(data) != FLASH_BYTES:
        raise RecoveryError('verification requires a full 512 KiB readback')
    expected = bytearray(image)
    if staged:
        expected[MARKER_OFFSET] = 0xFF
    if slot not in SLOTS:
        raise RecoveryError('slot must be A or B')
    start = SLOTS[slot]
    end = start + (SLOT_A_END if full_slot else erase_end(len(image)))
    if (data[start:start + len(image)] != expected
            or data[start + len(image):end] != b'\xff' * (end - start - len(image))):
        raise RecoveryError(f'slot {slot} image or erased tail differs from the expected bytes')
    if baseline:
        before = Path(baseline).read_bytes()
        if len(before) != FLASH_BYTES or before[:start] != data[:start] or before[end:] != data[end:]:
            raise RecoveryError('preserved flash differs from the baseline')
    return {'status': 'FLASH_MATCHED', 'imageSha256': plan['image']['sha256'],
            'slot': slot, 'fullSlot': full_slot, 'staged': staged, 'preservationVerified': bool(baseline),
            'bootVerified': False}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', help='print one JSON result object on stdout')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('ports', parents=[common], help='list serial ports')
    read = commands.add_parser('read', parents=[common], help='optional double flash read with pvvx bidirectional wiring')
    read.add_argument('--port', required=True)
    read.add_argument('--out', required=True, type=Path, help='private output outside Git; full flash by default')
    read.add_argument('--address', type=lambda v: int(v, 0), default=0)
    read.add_argument('--size', type=lambda v: int(v, 0), default=FLASH_BYTES)
    read.add_argument('--reader', type=Path, help='offline copy of the pinned TLSR825xComFlasher.py')
    read.add_argument('--halt', action='store_true', help='reset/halt CPU; upstream toggles RTS/DTR, see guide')
    inspect = commands.add_parser('inspect', parents=[common], help='identify both slots from a matching full double read')
    inspect.add_argument('--dump', type=Path, required=True)
    inspect.add_argument('--reference', action='append', default=[], metavar='NAME=FILE', help='known finalized BIN or native OTA; repeat for each firmware')
    inspect.add_argument('--replace', help='require a unique enabled slot matching this reference')
    inspect.add_argument('--expect-other', help='require the other disabled slot to match this reference')
    verify = commands.add_parser('verify', parents=[common], help='compare a full readback with a bundle')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--dump', type=Path, required=True)
    verify.add_argument('--staged', action='store_true', help='expect inactive marker, before activation')
    verify.add_argument('--baseline', type=Path, help='pre-write full dump for preserved-region comparison')
    verify.add_argument('--slot', choices=sorted(SLOTS), default='A')
    verify.add_argument('--full-slot', action='store_true', help='verify erased tail to the end of the selected slot')
    wire = commands.add_parser('wiring', parents=[common], help='how to connect a device')
    wire.add_argument('--device', choices=sorted(DEVICES))
    test = commands.add_parser('selftest', parents=[common],
                               help='adapter loopback test, TXD jumpered to RXD, no device')
    test.add_argument('--port', required=True)
    prep = commands.add_parser('prepare', parents=[common], help='make a recovery bundle')
    prep.add_argument('--device', default='bseed-ts011f', choices=sorted(DEVICES))
    prep.add_argument('--out', required=True, type=Path, help='new or empty bundle folder')
    prep.add_argument('--ota', type=Path, help='forward.ota from a CI artifact, or a stock OTA')
    prep.add_argument('--image', type=Path,
                      help='finalized image, such as glsd301p-ed_0x00000.bin from a Gledopto CI artifact')
    prep.add_argument('--manifest', type=Path, help='manifest.json next to forward.ota')
    prep.add_argument('--role-matrix', type=Path, help='ROLE_MATRIX.json from the same CI artifact')
    prep.add_argument('--expect-sha256', '--expect-ota-sha256', dest='expect_sha256',
                      help='SHA-256 of the input file')
    prep.add_argument('--expect-build', help='for example 1.2.5-bseedr22')
    show = commands.add_parser('plan', parents=[common], help='check a bundle and show what it would write')
    show.add_argument('--bundle', required=True, type=Path)
    show.add_argument('--slot', choices=sorted(SLOTS), default='A')
    show.add_argument('--full-slot', action='store_true')
    for name, text in (('flash', 'stage and activate in one run'),
                       ('stage', 'write the selected slot with its start marker off'),
                       ('activate', 'switch the start marker on and restart')):
        write = commands.add_parser(name, parents=[common], help=text)
        write.add_argument('--bundle', required=True, type=Path)
        write.add_argument('--port')
        write.add_argument('--confirm', help='ACTION-<first 8 hex digits of the image SHA-256>')
        write.add_argument('--slot', choices=sorted(SLOTS), default='A', help='target application slot; B requires a double-read baseline')
        write.add_argument('--full-slot', action='store_true', help='erase all 0x34000 bytes of this slot; never NVM')
        write.add_argument('--baseline', type=Path, help='original matching full double read, required for slot B')
        write.add_argument('--hold', action='store_true', help='keep CPU halted after activation for readback; no restart requested')
        write.add_argument('--power-cycle', action='store_true',
                           help='halt the chip while you switch 3.3 V on, instead of restarting it over SWS')
        write.add_argument('--dry-run', type=Path, metavar='FILE',
                           help='save the byte stream and a trace instead of opening a port')
        if name != 'activate':
            write.add_argument('--passes', type=int, default=PASSES,
                               help='3 (default) survives one lost erase or page; 1 and 2 do not')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    as_json = args.json

    def out(line):
        print(line, file=sys.stderr if as_json else sys.stdout)

    def emit(result):
        if as_json:
            print(json.dumps(result, indent=2))

    try:
        if args.action == 'inspect':
            result = inspect_slots(args.dump, args.reference, replace=args.replace, expect_other=args.expect_other)
            for slot in result['slots']:
                out(f'Slot {slot["slot"]}: marker {slot["markerState"]}, valid image {slot["validImage"]}, '
                    f'references {", ".join(slot["matches"]) or "none"}')
            out(f'Replacement slot: {result["replaceSlot"] or "not established"}. Boot remains unverified.')
            emit(result)
        elif args.action == 'read':
            out('Requires the documented pvvx bidirectional circuit. TX-only wiring cannot read.')
            result = read_flash(args.port, args.out, address=args.address, size=args.size,
                                reader=args.reader, halt=args.halt)
            out(f'Two reads match; private backup: {args.out}')
            emit(result)
        elif args.action == 'verify':
            result = verify_dump(args.bundle, args.dump, staged=args.staged, baseline=args.baseline,
                                 slot=args.slot, full_slot=args.full_slot)
            out('Flash bytes match. Boot and Zigbee operation remain unverified.')
            emit(result)
        elif args.action == 'ports':
            found = list_ports()
            for port in found:
                out(f'{port["device"]}\t{port["description"]}\t{port["hwid"]}')
            if not found:
                out('No serial ports found.')
            emit({'status': 'OK', 'ports': found})
        elif args.action == 'wiring':
            ids = [args.device] if args.device else list(DEVICES)
            for device_id in ids:
                out(wiring(device_id) if args.device else f'{device_id:<22} {DEVICES[device_id]["name"]}')
            if not args.device:
                out('Run wiring --device <name> for the pads and checks.')
            emit({'status': 'OK', 'devices': {i: DEVICES[i] for i in ids}})
        elif args.action == 'selftest':
            with open_port(args.port, timeout=0.2) as com:
                passed, got, sent = selftest(com)
            out(f'selftest {"PASS" if passed else "FAIL"}: {got} of {sent} bytes came back intact '
                f'at {UART_BAUD} baud')
            out('This checks the port, driver and baud rate only. Measure the TXD level separately.')
            emit({'status': 'PASS' if passed else 'FAIL', 'received': got, 'sent': sent})
            return 0 if passed else 1
        elif args.action == 'prepare':
            plan = prepare(args.ota, args.out, device=args.device, image_path=args.image,
                           manifest=args.manifest, role_matrix=args.role_matrix,
                           expect_sha256=args.expect_sha256, expect_build=args.expect_build)
            out(describe(plan))
            out(f'Bundle  {args.out}')
            emit({'status': 'OK', 'bundle': str(args.out), 'plan': plan})
        elif args.action == 'plan':
            plan, _ = load_bundle(args.bundle)
            out(describe(plan, slot=args.slot, full_slot=args.full_slot))
            log = receipts(args.bundle)
            for entry in log:
                last = entry.get('lastOperation') or {}
                where = f', last {last.get("operation")} {last.get("address", "")}'.rstrip() if last else ''
                out(f'Sent    {entry["utc"]} {entry["action"]} on {entry["port"]} ({entry["result"]}{where})')
            emit({'status': 'OK', 'plan': plan, 'receipts': log})
        else:
            if not args.dry_run and not args.port:
                raise RecoveryError('--port is required unless --dry-run is given')
            result = run_write(args.action, args.bundle, args.port, args.confirm,
                               getattr(args, 'passes', PASSES), power_cycle=args.power_cycle,
                               dry_run_path=args.dry_run, out=out, slot=args.slot,
                               full_slot=args.full_slot, baseline=args.baseline, hold=args.hold)
            emit(result if args.dry_run else {'status': 'SENT_UNVERIFIED', 'action': args.action,
                                              'receipts': result})
    except (RecoveryError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        emit({'status': 'BLOCKED', 'error': str(error)})
        return 2
    except KeyboardInterrupt:
        print('BLOCKED: interrupted. The selected slot may be partly erased; preserve evidence before recovery.', file=sys.stderr)
        emit({'status': 'INTERRUPTED', 'error': 'interrupted; selected slot may be partly erased'})
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
