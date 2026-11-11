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
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid
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
API_SCHEMA = 1
STATE_ROOT = Path.home() / '.telink-swire'
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
    def __init__(self, message, *, code='PRECONDITION_FAILED', details=None,
                 side_effects=False, retry_safe=True, exit_code=2):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.side_effects = side_effects
        self.retry_safe = retry_safe
        self.exit_code = exit_code


class AgentParser(argparse.ArgumentParser):
    def error(self, message):
        raise RecoveryError(message, code='INVALID_ARGUMENT')


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
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('sourceCommit', '')):
        raise RecoveryError('manifest has no exact source commit')
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
    if not re.fullmatch(r'[0-9a-f]{40}', matrix.get('sourceCommit', '')):
        raise RecoveryError('role matrix has no exact source commit')
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
    if device_id == 'generic-tlsr8258' and info['manufacturerCode'] in (0x1141, 0x124F):
        raise RecoveryError('recognized BSEED/Gledopto identity requires its guarded device profile')
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
    if device_id == 'bseed-ts011f':
        expected = {43556: ('OUTLET_BSEED_PM_TS011F', 'Router'),
                    65024: ('OUTLET_BSEED_PM_TS011F', 'EndDevice'),
                    43555: ('OUTLET_BSEED_TS011F', 'Router'),
                    65026: ('OUTLET_BSEED_TS011F', 'EndDevice')}[info['imageType']]
        matrix = source['roleMatrix']
        if (manifest.get('board'), matrix.get('role')) != expected:
            raise RecoveryError('BSEED board/role does not match the native image identity')
        if manifest.get('swBuildId') != matrix.get('build'):
            raise RecoveryError('BSEED manifest and role matrix build IDs differ')
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
        self.attach_only = False
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
        elif self.attach_only:
            self.note('attach-without-reset')
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


def estimate_seconds(image, passes=PASSES, *, slot='A', full_slot=False, hold=False):
    ticks = iter(range(0, 10 ** 9, 10 ** 6))
    writer = TxOnlyWriter(_Sink(), sleep=lambda _: None, clock=lambda: next(ticks),
                          slot=slot, full_slot=full_slot, hold=hold)
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
    path = bundle_log(folder)
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
    path = bundle_log(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as log:
        log.write(json.dumps(entry) + '\n')
    return entry


def fresh_stage(folder, image_sha, port, now=utc_now, *, slot='A', full_slot=False, baseline_sha=None):
    """True if the latest stage of this image finished, on this port, recently.

    The kit can be reused for several devices, so an old stage receipt says
    nothing about the chip on the wires now.
    """
    for entry in reversed(receipts(folder)):
        if entry.get('action') == 'activate':
            return False
        if entry.get('action') != 'stage':
            continue
        if entry.get('imageSha256') != image_sha:
            return False
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
        raise RecoveryError(f'--confirm must be {token}', code='INVALID_CONFIRMATION', details={'confirmationToken': token})
    if not interactive:
        raise RecoveryError(f'{action} needs --confirm {token} when not run interactively',
                            code='CONFIRMATION_REQUIRED', details={'confirmationToken': token})
    if ask(f'Type {token} to {action}: ').strip() != token:
        raise RecoveryError('cancelled')


def confirmation_token(action, image_sha, slot='A', full_slot=False):
    suffix = f'-{slot}-FULL' if full_slot else (f'-{slot}' if slot != 'A' else '')
    return f'{action.upper()}{suffix}-{image_sha[:8]}'


def selected_plan(plan, image, *, slot='A', full_slot=False, hold=False, passes=PASSES):
    start = SLOTS[slot]
    end = start + (SLOT_A_END if full_slot else erase_end(len(image)))
    result = {k: v for k, v in plan.items() if k not in ('slotA', 'neverWritten', 'estimatedSeconds')}
    result.update(apiSchema=API_SCHEMA, target={
        'slot': slot, 'eraseStart': start, 'eraseEndExclusive': end, 'eraseBytes': end - start,
        'programStart': start, 'programEndExclusive': start + len(image),
        'markerAddress': start + MARKER_OFFSET, 'fullSlot': full_slot,
        'preservedRanges': [[0, start], [end, 0x80000]], 'holdAfterActivation': hold},
        confirmationTokens={a: confirmation_token(a, plan['image']['sha256'], slot, full_slot)
                            for a in PHASES},
        estimatedSeconds=estimate_seconds(image, passes, slot=slot, full_slot=full_slot, hold=hold),
        requirements={'baselineRequired': slot == 'B', 'physicalTargetConfirmationRequired': True,
                      'readWiring': 'bidirectional', 'writeWiring': 'TX-only',
                      'verificationRequiredWhenBaselineSupplied': True,
                      'holdRequiredWhenBaselineSupplied': True}, bootVerified=False)
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def port_key(port):
    canonical = port.upper() if port.upper().startswith('COM') else str(Path(port).resolve())
    return sha256(canonical.encode())[:24]


def bundle_log(folder):
    return STATE_ROOT / 'bundles' / sha256(str(Path(folder).resolve()).encode()) / BUNDLE_LOG


def authority_path(port):
    return STATE_ROOT / 'ports' / (port_key(port) + '.json')


def default_session(port):
    return STATE_ROOT / 'sessions' / (port_key(port) + '.json')


class MetadataGate:
    """Short OS-owned lock serializing port-lock create/remove/unlock races.

    The stable gate file is never deleted; the OS releases its lock on crash.
    The separate long-lived JSON lock still requires explicit crash recovery.
    """
    def __init__(self, port):
        self.path = STATE_ROOT / 'locks' / (port_key(port) + '.gate')

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.stream.seek(0, 2) == 0:
            self.stream.write(b'\0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise RecoveryError('port metadata is busy; no hardware was opened', code='PORT_METADATA_BUSY') from error
        return self

    def __exit__(self, *_):
        try:
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


class PortGuard:
    """Cross-process exclusion. Crash residue is never silently discarded."""
    def __init__(self, port):
        self.path = STATE_ROOT / 'locks' / (port_key(port) + '.json')
        self.owner = {'port': port, 'pid': os.getpid(), 'nonce': uuid.uuid4().hex,
                      'utc': utc_now().isoformat()}

    def __enter__(self):
        with MetadataGate(self.owner['port']):
            try:
                with self.path.open('x', encoding='utf-8') as stream:
                    json.dump(self.owner, stream)
            except FileExistsError as error:
                raise RecoveryError('port has an active or abandoned tool lock; inspect status before unlocking',
                                    code='PORT_LOCKED', details={'lock': str(self.path)}, retry_safe=False) from error
        return self

    def __exit__(self, *_):
        try:
            with MetadataGate(self.owner['port']):
                if read_json(self.path) != self.owner:
                    raise RecoveryError('port lock ownership changed; do not delete another owner lock', code='PORT_LOCKED')
                self.path.unlink()
        except (RecoveryError, OSError, ValueError) as error:
            raise RecoveryError('port lock cleanup did not finish; inspect saved operation result before retrying',
                                code='PORT_LOCKED', side_effects=True, retry_safe=False,
                                details={'port': self.owner['port'], 'lock': str(self.path)}) from error


def session_status(path):
    path = Path(path)
    state = read_json(path)
    steps = {'READY': ['stage'], 'STAGED': ['read', 'verify'], 'VERIFIED': ['activate'],
             'HELD': ['read', 'verify'], 'ACTIVATED_VERIFIED': ['manual_bench_start'],
             'BOOT_REQUESTED': ['live_device_acceptance'], 'NEEDS_RECONCILIATION': ['read', 'inspect', 'reconcile', 'close'],
             'VERIFICATION_FAILED': ['read', 'inspect', 'reconcile', 'close'], 'CLOSED': ['new_session']}
    result = dict(state, session=str(path), nextActions=steps.get(state['phase'], []),
                  bootVerified=False, apiSchema=API_SCHEMA, physicalIdentityVerified=False,
                  closeConfirmationToken='CLOSE-' + state['id'][:8])
    if state['phase'] == 'STAGED' and not state['context']['verificationRequired']:
        result['nextActions'].append('activate_unverified')
    lock = STATE_ROOT / 'locks' / (port_key(state['context']['port']) + '.json')
    result['portLock'] = read_json(lock) if lock.exists() else None
    return result


def save_session(path, state):
    state['updatedUtc'] = utc_now().isoformat()
    atomic_json(path, state)
    atomic_json(authority_path(state['context']['port']), {'session': str(Path(path).resolve()),
                'phase': state['phase'], 'targetId': state['context']['targetId']})


def prepare_session(path, context, action):
    path = private_output(path)
    authority = authority_path(context['port'])
    if authority.exists():
        owner = read_json(authority)
        if owner['session'] != str(path) and owner['phase'] != 'CLOSED':
            raise RecoveryError('another recovery session owns this port; resume or explicitly close it',
                                code='SESSION_CONFLICT', details=owner, retry_safe=False)
    if path.exists():
        state = read_json(path)
        if state.get('context') != context:
            raise RecoveryError('session target/image/layout changed; do not reuse this session', code='SESSION_MISMATCH')
        return path, state
    if action == 'activate':
        raise RecoveryError('activate needs a finished stage in this recovery session', code='STAGE_REQUIRED')
    state = {'apiSchema': API_SCHEMA, 'id': uuid.uuid4().hex, 'context': context,
             'phase': 'READY', 'operations': {}, 'createdUtc': utc_now().isoformat()}
    save_session(path, state)
    return path, state


def session_context(folder, plan, port, slot, full_slot, baseline, target_id):
    return {'bundle': str(Path(folder).resolve()), 'imageSha256': plan['image']['sha256'],
            'port': port, 'slot': slot, 'fullSlot': full_slot, 'targetId': target_id,
            'baseline': str(Path(baseline).resolve()) if baseline else None,
            'baselineSha256': sha256(matched_backup(baseline, port=port)) if baseline else None,
            'verificationRequired': bool(baseline)}


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
              slot='A', full_slot=False, baseline=None, hold=False, session=None,
              operation_id=None, target_id='operator-confirmed'):
    plan, image = load_bundle(folder)
    image_sha = plan['image']['sha256']
    if passes not in SCHEDULES or slot not in SLOTS:
        raise RecoveryError('invalid passes or slot', code='INVALID_ARGUMENT')
    if slot == 'B' and not baseline:
        raise RecoveryError('slot B requires --baseline from a matching double read with slot A disabled')
    if baseline:
        before = matched_backup(baseline, port=None if dry_run_path else port)
        if slot == 'B' and before[8:12] != b'\x00NLT':
            raise RecoveryError('slot A is not explicitly disabled; slot B cannot take boot priority. Stop and inspect')
        state_of_slots = inspect_slots(baseline)
        enabled = [s for s in state_of_slots['slots'] if s['markerState'] == 'enabled']
        if len(enabled) != 1 or enabled[0]['slot'] != slot or state_of_slots['legacyMarkers']:
            raise RecoveryError('baseline does not establish this unique active slot; inspect before choosing a write target',
                                code='SLOT_SELECTION_UNCONFIRMED')
        if not full_slot:
            raise RecoveryError('in-place recovery from a baseline requires --full-slot', code='FULL_SLOT_REQUIRED')
        if slot == 'B' and (not state_of_slots['slots'][0]['validImage'] or state_of_slots['legacyMarkers']):
            raise RecoveryError('slot A has an unknown image or legacy layout marker; stop before writing B')
        if action == 'flash':
            raise RecoveryError('a baseline selects verified recovery: use stage, read, verify, activate separately',
                                code='VERIFICATION_REQUIRED')
        if action == 'activate' and not hold:
            raise RecoveryError('verified in-place activation requires --hold for readback before bench start',
                                code='HOLD_REQUIRED')
    if dry_run_path:
        return dry_run(action, plan, image, passes, power_cycle, dry_run_path, out,
                       slot=slot, full_slot=full_slot, hold=hold)
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if power_cycle and (not interactive or action == 'activate'):
        raise RecoveryError('--power-cycle requires an interactive stage/flash; activation never resets the chip')
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
        raise RecoveryError('invalid serial port', code='INVALID_ARGUMENT')
    operation_id = operation_id or uuid.uuid4().hex
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', operation_id):
        raise RecoveryError('operation ID must be 1-96 letters, numbers, dot, dash or underscore')
    context = session_context(folder, plan, port, slot, full_slot, baseline, target_id)
    path = Path(session) if session else default_session(port)
    request = dict(context, action=action, passes=passes, hold=hold, powerCycle=power_cycle)
    with PortGuard(port):
        path, state = prepare_session(path, context, action)
        previous = state['operations'].get(operation_id)
        if previous:
            if previous['request'] != request:
                raise RecoveryError('operation ID was already used for different arguments', code='OPERATION_CONFLICT')
            if previous['status'] == 'COMPLETED':
                return dict(previous['result'], replayed=True)
            raise RecoveryError('this operation started but did not complete; inspect and reconcile before writing',
                                code='RECONCILIATION_REQUIRED', retry_safe=False,
                                side_effects=True, details={'session': str(path), 'operationId': operation_id})
        allowed = {'stage': {'READY'}, 'flash': {'READY'}, 'activate': {'STAGED', 'VERIFIED'}}
        if state['phase'] not in allowed[action]:
            raise RecoveryError(f'cannot {action} from {state["phase"]}; inspect status and reconcile or close',
                                code='INVALID_PHASE', retry_safe=False, details={'session': str(path)})
        if action == 'activate':
            if context['verificationRequired'] and state['phase'] != 'VERIFIED':
                raise RecoveryError('activate requires a successful staged readback verification in this session',
                                    code='VERIFICATION_REQUIRED', details={'session': str(path)})
            age = (now() - datetime.datetime.fromisoformat(state['stageUtc'])).total_seconds()
            if not 0 <= age <= STAGE_FRESH_SECONDS:
                raise RecoveryError('stage is no longer fresh; read and reconcile before activation', code='STAGE_EXPIRED')
        out(describe(plan, slot=slot, full_slot=full_slot))
        for line in CHECKLIST:
            out(f'  - {line}')
        token = confirmation_token(action, image_sha, slot, full_slot)
        confirm(token.rsplit('-', 1)[0], image_sha, provided, interactive)
        if power_cycle:
            ask('Disconnect only 3.3 V, keep SWS/GND, then press Enter: ')
        # Persist intent before opening the port: even a crash at open leaves a
        # conservatively uncertain operation. A new operation ID cannot bypass it.
        state['operations'][operation_id] = {'request': request, 'status': 'STARTED'}
        state['phase'] = 'NEEDS_RECONCILIATION'
        state['activeOperation'] = operation_id
        save_session(path, state)
        writer = None
        entries = []
        try:
            with opener(port) as com:
                def progress(number, total, address, end):
                    state['progress'] = {'pass': number, 'passes': total, 'offset': address, 'end': end}
                    save_session(path, state)
                    out(f'pass {number}/{total}: {address:#x}/{end:#x}')

                writer = TxOnlyWriter(com, power_on=(lambda: out('Connect 3.3 V NOW')) if power_cycle else None,
                                      progress=progress, slot=slot, full_slot=full_slot, hold=hold)
                writer.baseline_sha = context['baselineSha256']
                # A separate activation must reassert HALT without software reset.
                # Never infer observed target execution from an in-memory flag.
                writer.attach_only = action == 'activate'
                for phase in PHASES[action]:
                    state['activePhase'] = phase
                    save_session(path, state)
                    record(folder, phase, port, image_sha, writer, 'STARTED', passes, now, power_cycle)
                    try:
                        run_phase(writer, phase, image, passes)
                    except BaseException as error:
                        record(folder, phase, port, image_sha, writer,
                               f'INTERRUPTED: {type(error).__name__}', passes, now, power_cycle)
                        raise
                    entries.append(record(folder, phase, port, image_sha, writer, 'SENT_UNVERIFIED', passes,
                                          now, power_cycle))
                    if phase == 'stage':
                        state['stageUtc'] = now().isoformat()
            state['phase'] = 'STAGED' if action == 'stage' else ('HELD' if hold else 'BOOT_REQUESTED')
            result = {'status': 'SENT_UNVERIFIED', 'action': action, 'session': str(path),
                      'operationId': operation_id, 'targetId': target_id, 'slot': slot,
                      'phase': state['phase'], 'receipts': entries, 'bootVerified': False,
                      'flashVerified': False, 'retrySafe': True, 'replayed': False}
            state['operations'][operation_id].update(status='COMPLETED', result=result)
            save_session(path, state)
            return result
        except BaseException as error:
            state['phase'] = 'NEEDS_RECONCILIATION'
            state['operations'][operation_id].update(status='UNCERTAIN', error=type(error).__name__)
            save_session(path, state)
            raise RecoveryError(f'{action} did not finish; flash or debug state may have changed. Do not retry the write',
                                code='INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'WRITE_UNCERTAIN',
                                details={'session': str(path), 'operationId': operation_id,
                                         'activePhase': state.get('activePhase'),
                                         'lastOperation': writer.last if writer else None},
                                side_effects=True, retry_safe=False,
                                exit_code=130 if isinstance(error, KeyboardInterrupt) else 3) from error


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


def read_flash(port, output, **kwargs):
    with PortGuard(port):
        authority = authority_path(port)
        if kwargs.get('halt') and authority.exists() and read_json(authority)['phase'] != 'CLOSED':
            raise RecoveryError('reset/halt is forbidden during an active recovery session; use a no-reset read',
                                code='RESET_FORBIDDEN', retry_safe=False)
        return _read_flash(port, output, **kwargs)


def _read_flash(port, output, *, address=0, size=FLASH_BYTES, reader=None,
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
    logs = [output.with_name(output.name + f'.reader-{i}.log') for i in (1, 2)]
    if any(p.exists() for p in (output, second, receipt, *logs)):
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
              'utc': utc_now().isoformat(), 'reads': [], 'readerRuns': [], 'receipt': str(receipt)}
    atomic_json(receipt, result)
    try:
        for index, target in enumerate((output, second)):
            command = [sys.executable, str(reader), '-p', port, '-b', '921600']
            if halt:
                command += ['-t', '3000']
            command += ['rf', hex(address), hex(size), str(target)]
            result['activeRead'] = index + 1
            atomic_json(receipt, result)
            stdout, stderr, return_code = '', '', None
            try:
                completed = runner(command, capture_output=True, text=True, timeout=1800)
                stdout, stderr = getattr(completed, 'stdout', '') or '', getattr(completed, 'stderr', '') or ''
                return_code = completed.returncode
            except subprocess.TimeoutExpired as error:
                stdout, stderr, return_code = error.stdout or '', error.stderr or '', None
                raise RecoveryError('reader timed out; diagnostics and partial dump retained', code='READ_TIMEOUT',
                                    retry_safe=False) from error
            finally:
                # Bounded, private diagnostics. Never send raw reader output to a
                # public CI log or assume failure output is valid protocol JSON.
                def as_text(value):
                    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else value
                logs[index].write_text(as_text(locals().get('stdout', ''))[-32768:] + '\nSTDERR\n'
                                       + as_text(locals().get('stderr', ''))[-32768:], encoding='utf-8')
                result['readerRuns'].append({'read': index + 1, 'exitCode': locals().get('return_code'),
                                             'diagnostics': str(logs[index])})
            if completed.returncode != 0:
                raise RecoveryError('bidirectional read failed; check the pvvx circuit, voltage and contact. '
                                    'No TX-only fallback or write was attempted', code='READ_FAILED', retry_safe=False)
            data = target.read_bytes()
            if len(data) != size:
                raise RecoveryError('short flash read; retained for diagnosis')
            result['reads'].append({'file': str(target), 'sha256': sha256(data)})
        if result['reads'][0]['sha256'] != result['reads'][1]['sha256']:
            raise RecoveryError('two flash reads differ; no trustworthy backup established')
        result['status'] = 'READ_MATCHED'
        return result
    except BaseException as error:
        result['status'] = 'READ_FAILED'
        result['errorCode'] = getattr(error, 'code', 'READ_INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'READ_FAILED')
        raise RecoveryError(str(error), code=result['errorCode'], retry_safe=False,
                            side_effects=True,
                            details={'receipt': str(receipt), 'readerRuns': result['readerRuns'],
                                     'flashWriteAttempted': False, 'debugStateMayHaveChanged': True},
                            exit_code=130 if isinstance(error, KeyboardInterrupt) else 2) from error
    finally:
        atomic_json(receipt, result)


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


def verify_dump(folder, dump, *, staged=False, baseline=None, slot='A', full_slot=False,
                session=None, reconcile=False):
    """Offline comparison of a full readback; no device is opened or restarted."""
    if session:
        return verify_session(session, dump, folder=folder, staged=staged, baseline=baseline,
                              slot=slot, full_slot=full_slot, reconcile=reconcile)
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


def verify_session(path, dump, *, folder, staged, baseline, slot, full_slot, reconcile=False):
    state = read_json(path)
    context = state['context']
    with PortGuard(context['port']):
        owner = read_json(authority_path(context['port']))
        if owner['session'] != str(Path(path).resolve()):
            raise RecoveryError('another session owns this port; copied sessions cannot take ownership', code='SESSION_CONFLICT')
        # Re-read under the lock, so a concurrent operation cannot change phase.
        state = read_json(path)
        expected = (context['bundle'], context['slot'], context['fullSlot'], context['baseline'])
        actual = (str(Path(folder).resolve()), slot, full_slot,
                  str(Path(baseline).resolve()) if baseline else None)
        if actual != expected:
            raise RecoveryError('verification arguments differ from the session', code='SESSION_MISMATCH')
        if state['phase'] in ('CLOSED', 'READY', 'BOOT_REQUESTED'):
            raise RecoveryError('session is not awaiting held-flash verification', code='INVALID_PHASE')
        if state['phase'] == 'NEEDS_RECONCILIATION' and not reconcile:
            raise RecoveryError('interrupted operation requires reconcile with a fresh read', code='RECONCILIATION_REQUIRED')
        matched_backup(dump, port=context['port'])
        evidence = read_json(Path(dump).with_name(Path(dump).name + '.json'))
        if context['baselineSha256'] and sha256(matched_backup(baseline, port=context['port'])) != context['baselineSha256']:
            raise RecoveryError('original baseline changed', code='BASELINE_CHANGED')
        plan, _ = load_bundle(folder)
        if plan['image']['sha256'] != context['imageSha256']:
            raise RecoveryError('session image changed', code='SESSION_MISMATCH')
        prior = state.get('lastVerification', {})
        if (state['phase'] == ('VERIFIED' if staged else 'ACTIVATED_VERIFIED')
                and prior.get('dump') == str(Path(dump).resolve())
                and prior.get('dumpSha256') == sha256(Path(dump).read_bytes())
                and prior.get('staged') == staged):
            return dict(prior, session=str(path), phase=state['phase'], replayed=True,
                        nextActions=session_status(path)['nextActions'])
        threshold = max(datetime.datetime.fromisoformat(state['updatedUtc']),
                        datetime.datetime.fromisoformat(state.get('stageUtc', state['createdUtc'])))
        if (datetime.datetime.fromisoformat(evidence['utc']) < threshold
                or evidence.get('halt')):
            raise RecoveryError('use a fresh no-reset double read after the pending operation', code='STALE_EVIDENCE')
        try:
            result = verify_dump(folder, dump, staged=staged, baseline=baseline, slot=slot, full_slot=full_slot)
        except (RecoveryError, OSError, ValueError) as error:
            state['phase'] = 'VERIFICATION_FAILED'
            state['lastVerification'] = {'status': 'FAILED', 'error': str(error), 'dump': str(dump)}
            save_session(path, state)
            raise
        if not staged and state['phase'] in ('STAGED', 'VERIFIED') and not reconcile:
            raise RecoveryError('activation was not sent by this session; reconcile observed state explicitly', code='INVALID_PHASE')
        if staged and state['phase'] in ('HELD', 'ACTIVATED_VERIFIED') and not reconcile:
            raise RecoveryError('marker is unexpectedly inactive after activation; reconcile explicitly', code='INVALID_PHASE')
        state['phase'] = 'VERIFIED' if staged else 'ACTIVATED_VERIFIED'
        state['lastVerification'] = dict(result, dumpSha256=sha256(Path(dump).read_bytes()),
                                         dump=str(Path(dump).resolve()), readReceipt=evidence)
        if staged and reconcile:
            state['stageUtc'] = utc_now().isoformat()
        save_session(path, state)
        return dict(result, session=str(path), phase=state['phase'], nextActions=session_status(path)['nextActions'])


def close_session(path, confirmation, reason):
    state = read_json(path)
    if not reason or confirmation != 'CLOSE-' + state['id'][:8]:
        raise RecoveryError('closing requires --reason and --confirm CLOSE-<session id prefix>', code='CONFIRMATION_REQUIRED')
    with PortGuard(state['context']['port']):
        owner = read_json(authority_path(state['context']['port']))
        if owner['session'] != str(Path(path).resolve()):
            raise RecoveryError('another session owns the port', code='SESSION_CONFLICT')
        state = read_json(path)
        state['phase'] = 'CLOSED'
        state['closeReason'] = reason
        state['closureIsHardwareAcceptance'] = False
        save_session(path, state)
    return session_status(path)


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied is not evidence of a dead process.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def unlock_port(port, confirmation):
    path = STATE_ROOT / 'locks' / (port_key(port) + '.json')
    with MetadataGate(port):
        owner = read_json(path)
        if confirmation != 'UNLOCK-' + owner['nonce'] or process_alive(owner['pid']):
            raise RecoveryError('unlock requires the exact lock token and a dead owner process', code='PORT_LOCKED')
        # Serialize against new owners; never rename a replacement lock after
        # another agent has already released the abandoned one.
        archive = path.with_name(path.stem + '.released-' + owner['nonce'])
        path.rename(archive)
    return {'status': 'LOCK_RELEASED', 'archive': str(archive), 'hardwareReconciled': False}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='one JSON result, never prompt')
    common.add_argument('--non-interactive', action='store_true', default=argparse.SUPPRESS)
    common.add_argument('--session', type=Path, default=argparse.SUPPRESS, help='private durable recovery session outside Git')
    parser = AgentParser(description=__doc__, parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('ports', parents=[common], help='list serial ports')
    commands.add_parser('capabilities', parents=[common], help='machine-readable interface and safety contract')
    status = commands.add_parser('status', parents=[common], help='read session or port state without opening hardware')
    status.add_argument('--port')
    status.add_argument('--read-receipt', type=Path)
    close = commands.add_parser('close', parents=[common], help='explicitly stop a session; never claims recovery')
    close.add_argument('--confirm', required=True)
    close.add_argument('--reason', required=True)
    unlock = commands.add_parser('unlock', parents=[common], help='archive a dead process lock without reconciling hardware')
    unlock.add_argument('--port', required=True)
    unlock.add_argument('--confirm', required=True)
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
    verify.add_argument('--bundle', type=Path)
    verify.add_argument('--dump', type=Path, required=True)
    verify.add_argument('--staged', action='store_true', help='expect inactive marker, before activation')
    verify.add_argument('--baseline', type=Path, help='pre-write full dump for preserved-region comparison')
    verify.add_argument('--slot', choices=sorted(SLOTS))
    verify.add_argument('--full-slot', action='store_true', default=None, help='verify erased tail to the end of the selected slot')
    reconcile = commands.add_parser('reconcile', parents=[common], help='resolve uncertain write from a fresh no-reset double read')
    reconcile.add_argument('--dump', required=True, type=Path)
    reconcile.add_argument('--staged', action='store_true')
    reconcile.set_defaults(bundle=None, baseline=None, slot=None, full_slot=None)
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
        write.add_argument('--bundle', type=Path)
        write.add_argument('--port')
        write.add_argument('--confirm', help='ACTION-<first 8 hex digits of the image SHA-256>')
        write.add_argument('--slot', choices=sorted(SLOTS), help='target application slot; B requires a double-read baseline')
        write.add_argument('--full-slot', action='store_true', default=None, help='erase all 0x34000 bytes of this slot; never NVM')
        write.add_argument('--baseline', type=Path, help='original matching full double read; selects required verification')
        write.add_argument('--operation-id', help='stable unique ID for this request; required for JSON writes')
        write.add_argument('--target-id', help='operator-confirmed exact target identity; required for a new JSON session')
        write.add_argument('--hold', action='store_true', help='keep CPU halted after activation for readback; no restart requested')
        write.add_argument('--power-cycle', action='store_true',
                           help='halt the chip while you switch 3.3 V on, instead of restarting it over SWS')
        write.add_argument('--dry-run', type=Path, metavar='FILE',
                           help='save the byte stream and a trace instead of opening a port')
        if name != 'activate':
            write.add_argument('--passes', type=int, default=PASSES,
                               help='3 (default) survives one lost erase or page; 1 and 2 do not')
    return parser


def session_arguments(args):
    state = read_json(args.session) if args.session and args.session.exists() else None
    context = state['context'] if state else {}
    for attribute, key, default in (('bundle', 'bundle', None), ('port', 'port', None),
                                     ('slot', 'slot', 'A'), ('full_slot', 'fullSlot', False),
                                     ('baseline', 'baseline', None), ('target_id', 'targetId', None)):
        if getattr(args, attribute, None) is None:
            setattr(args, attribute, context.get(key, default))
    if not args.bundle:
        raise RecoveryError('--bundle is required for a new session or standalone verification', code='INVALID_ARGUMENT')
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = '--json' in argv
    action = None

    def out(line):
        print(line, file=sys.stderr if as_json else sys.stdout, flush=True)

    def emit(result):
        if as_json:
            print(json.dumps({'apiSchema': API_SCHEMA, **result}, indent=2))

    try:
        args = build_parser().parse_args(argv)
        if not hasattr(args, 'session'):
            args.session = None
        action = args.action
        interactive = not (as_json or getattr(args, 'non_interactive', False)) and sys.stdin.isatty()
        if action == 'capabilities':
            result = {'status': 'OK', 'commands': ['prepare', 'plan', 'ports', 'wiring', 'selftest',
                       'stage', 'read', 'inspect', 'verify', 'activate', 'flash', 'status', 'reconcile', 'close', 'unlock'],
                      'jsonNeverPrompts': True, 'idempotentWrites': 'same session + operation ID + exact request',
                      'readOptional': True, 'baselineRequiresVerification': True,
                      'physicalTargetIdentification': 'operator-confirmed; TX-only cannot identify the chip',
                      'successfulWriteProof': 'SENT_UNVERIFIED', 'bootVerified': False,
                      'exitCodes': {'0': 'completed or safely replayed result; check status for proof level',
                                    '1': 'selftest failure or internal error', '2': 'precondition/read/argument failure',
                                    '3': 'uncertain write; do not retry', '130': 'interrupted; inspect sideEffectsPossible'},
                      'stateRoot': str(STATE_ROOT)}
        elif action == 'status':
            if args.read_receipt:
                result = read_json(args.read_receipt)
            elif args.session:
                result = dict(status='OK', **session_status(args.session))
            elif args.port:
                authority = authority_path(args.port)
                lock = STATE_ROOT / 'locks' / (port_key(args.port) + '.json')
                result = {'status': 'OK', 'authority': read_json(authority) if authority.exists() else None,
                          'portLock': read_json(lock) if lock.exists() else None}
            else:
                raise RecoveryError('status needs --session, --port or --read-receipt', code='INVALID_ARGUMENT')
        elif action == 'close':
            if not args.session:
                raise RecoveryError('close needs --session', code='INVALID_ARGUMENT')
            result = dict(status='CLOSED', **close_session(args.session, args.confirm, args.reason))
        elif action == 'unlock':
            result = unlock_port(args.port, args.confirm)
        elif action == 'inspect':
            result = inspect_slots(args.dump, args.reference, replace=args.replace, expect_other=args.expect_other)
            for slot in result['slots']:
                out(f'Slot {slot["slot"]}: {slot["markerState"]}, valid={slot["validImage"]}, matches={slot["matches"]}')
            out(f'Replacement slot: {result["replaceSlot"] or "not established"}')
        elif action == 'read':
            result = read_flash(args.port, args.out, address=args.address, size=args.size,
                                reader=args.reader, halt=args.halt)
            out(f'Two reads match; private backup: {args.out}')
        elif action in ('verify', 'reconcile'):
            if action == 'reconcile' and not args.session:
                raise RecoveryError('reconcile needs --session', code='INVALID_ARGUMENT')
            args = session_arguments(args)
            result = verify_dump(args.bundle, args.dump, staged=args.staged, baseline=args.baseline,
                                 slot=args.slot, full_slot=args.full_slot, session=args.session,
                                 reconcile=action == 'reconcile')
            out('Flash bytes match. Boot and Zigbee operation remain unverified.')
        elif action == 'ports':
            found = list_ports()
            for port in found:
                out(f'{port["device"]}\t{port["description"]}\t{port["hwid"]}')
            result = {'status': 'OK', 'ports': found}
        elif action == 'wiring':
            ids = [args.device] if args.device else list(DEVICES)
            for device_id in ids:
                out(wiring(device_id) if args.device else f'{device_id}: {DEVICES[device_id]["name"]}')
            result = {'status': 'OK', 'devices': {i: DEVICES[i] for i in ids}}
        elif action == 'selftest':
            with PortGuard(args.port):
                authority = authority_path(args.port)
                if authority.exists() and read_json(authority)['phase'] != 'CLOSED':
                    raise RecoveryError('selftest is adapter-only and forbidden during an active device session', code='INVALID_PHASE')
                with open_port(args.port, timeout=0.2) as com:
                    passed, got, sent = selftest(com)
            result = {'status': 'PASS' if passed else 'FAIL', 'received': got, 'sent': sent}
            emit(result)
            return 0 if passed else 1
        elif action == 'prepare':
            plan = prepare(args.ota, args.out, device=args.device, image_path=args.image,
                           manifest=args.manifest, role_matrix=args.role_matrix,
                           expect_sha256=args.expect_sha256, expect_build=args.expect_build)
            out(describe(plan))
            result = {'status': 'OK', 'bundle': str(args.out), 'plan': plan}
        elif action == 'plan':
            plan, image = load_bundle(args.bundle)
            runtime = selected_plan(plan, image, slot=args.slot, full_slot=args.full_slot)
            out(describe(plan, slot=args.slot, full_slot=args.full_slot))
            result = {'status': 'OK', 'plan': runtime, 'receipts': receipts(args.bundle)}
        else:
            args = session_arguments(args)
            if not args.dry_run and not args.port:
                raise RecoveryError('--port is required unless --dry-run is given', code='INVALID_ARGUMENT')
            if as_json and not args.dry_run and (not args.session or not args.operation_id or not args.target_id):
                raise RecoveryError('JSON writes need --session, --operation-id and a confirmed --target-id for new sessions',
                                    code='AGENT_CONTEXT_REQUIRED')
            result = run_write(action, args.bundle, args.port, args.confirm, getattr(args, 'passes', PASSES),
                               power_cycle=args.power_cycle, dry_run_path=args.dry_run, out=out,
                               slot=args.slot, full_slot=args.full_slot, baseline=args.baseline, hold=args.hold,
                               interactive=interactive, session=args.session, operation_id=args.operation_id,
                               target_id=args.target_id or 'operator-confirmed')
            out(f'{action}: {result["status"]}; phase={result.get("phase", "simulation")}; session={result.get("session", "none")}')
        emit(result)
        return 0
    except (RecoveryError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        emit({'status': 'UNCERTAIN' if getattr(error, 'side_effects', False) else 'BLOCKED',
              'action': action, 'errorCode': getattr(error, 'code', 'INVALID_DATA'), 'error': str(error),
              'details': getattr(error, 'details', {}), 'retrySafe': getattr(error, 'retry_safe', True),
              'sideEffectsPossible': getattr(error, 'side_effects', False), 'bootVerified': False})
        return getattr(error, 'exit_code', 2)
    except KeyboardInterrupt:
        emit({'status': 'INTERRUPTED', 'action': action, 'errorCode': 'INTERRUPTED',
              'retrySafe': False, 'sideEffectsPossible': False, 'bootVerified': False})
        return 130
    except Exception as error:
        print(f'INTERNAL ERROR: {type(error).__name__}: {error}', file=sys.stderr)
        emit({'status': 'UNCERTAIN', 'action': action, 'errorCode': 'INTERNAL_ERROR',
              'error': str(error), 'retrySafe': False, 'sideEffectsPossible': True, 'bootVerified': False})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
