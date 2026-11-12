#!/usr/bin/env python3
"""Wired recovery of Telink TLSR8258 Zigbee devices with a USB-UART adapter.

Writes a firmware image into slot A (0x00000) or B (0x40000) of a 512 KiB TLSR8258
without a bootloader. The writer uses pvvx's TX-only SWire method and cannot
receive acknowledgements. Optional bidirectional readback verifies flash bytes;
real device boot and Zigbee operation still require independent evidence.

Typical use
  wiring --device gledopto-gl-sd-301p          how to connect, step by step
  selftest --port COM5                         adapter check, TXD jumpered to RXD
  prepare --device ... --out kit               make a bundle (offline, or the pinned download)
  flash --bundle kit --port COM5               write it

Commands
  ports      list serial ports
  wiring     pads, checks and notes for a device (all devices without --device)
  selftest   loopback test of the adapter alone (TXD jumpered to RXD)
  test-link  qualify a CP2102 resistor/RX junction, then actual target replies
  watch-link bounded target probing with USB reconnect waiting and error backoff
  fetch-reader  download/cache the pinned pvvx reader without accessing hardware
  prepare    make a recovery bundle from a Zigbee OTA file or a finalized image
  boards     find a tuya-zigbee-switch board (Avatto, Moes, ...) in the released OTA index
  plan       check a bundle and show what it writes and what was sent before
  flash      stage, then activate, in one run
  stage      erase the selected slot and write with its boot marker off
  inspect    identify both slots from a matching full double read
  activate   switch the boot marker on (one byte, no erase) and restart
  read       optional matching double read with a bidirectional SWire circuit
  verify     compare readback and advance a verified recovery session
  status     inspect durable session progress without opening hardware
  reconcile  resolve uncertain writes using a fresh no-reset double read
  capabilities  discover the machine contract, proof levels and exit codes

flash, stage and activate take --dry-run FILE to save the exact byte stream
and an operation trace instead of opening a port. Every command takes --json
for one machine-readable result object on stdout.

Never written: the other slot, either NVM area, IEEE address or factory data.
Default target is A; B requires a matching baseline with A disabled. Pyserial
is needed for port access: python -m pip install pyserial. Read README.md in a
downloaded kit, or docs/flashing/swire_recovery.md in a checkout, for the complete
agent protocol and close/unlock procedures. No command infers physical readiness.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
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
    'GND -> GND and approved 3V3 -> 3V3; never +5V. TX-only: TXD -> SWS, RXD unused. '
    'Qualified bidirectional circuit: TXD through 1.2k to the shared RXD/SWS node.',
    'Every lead proven by a continuity test; clips hold on their own; nothing bridges neighbouring pads.',
)
GUIDE = 'docs/flashing/swire_recovery.md'

GLEDOPTO_STOCK_COMMIT = '895567036301d25da68161358f96a37971488a08'
R19_RECOVERY = {
    'sha256': '3789b565a81a518c197c895809cf10a4480d50bacd821f65359c44141115fb8b',
    'bytes': 209604, 'fileVersion': 0x12053021, 'manufacturerCode': 0x1141, 'imageType': 43556,
    'build': '1.2.5-bseedr19', 'board': 'OUTLET_BSEED_PM_TS011F', 'role': 'Router',
    'sourceCommit': '1cc08a92d4dd68ff3607dc224f707c3e757d4ec8',
    'otaSha256': 'd17571a1d996ff798f1b6334cac1d799fbc1c6125a0212741aca89446053fc95',
    'qualification': 'historical rollback only; not a current release or fleet acceptance',
}
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
            # Exact frozen candidate made available as a wired artifact. This
            # format/provenance admission grants no live flash authorization.
            # Exact bytes of glsd301p-ed_0x00000.bin from
            # analienx/gledopto 1dec734c815fe7721958be818dfadf88bae944fe, run 42973182814.
            '8d90c745734e38c8686a8649bd859c77508bfc3c453aef43f3bf379e694837ff':
                'GLSD-ED-008 custom End Device (gledopto 1dec734)',
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
            'Admitted images: the pinned vendor stock image and the pinned GLSD-ED-008 custom build. '
            'Any other custom build is refused until its SHA-256 is added here.',
            'GLSD-ED-008 clears the Zigbee stack state once at its first start: pair the dimmer again. '
            'Returning to stock also needs a new pairing.',
            'Preserving flash does not prove the retained network record is compatible with stock firmware.',
        ],
    },
    'tuya-zigbee-switch': {
        'name': 'Board built by tuya-zigbee-switch (Avatto, MHCOZY, Moes, BSEED switches, ...)',
        'manufacturerCodes': [0x1141],
        'index': 'board',
        # Boards with their own gated firmware line in this fork. Refused by
        # name, not image type: upstream and fork image types collide (upstream
        # MODULE_TUYA_NOVATO_QS_S05_TS0001 and the fork's TS0726 3-gang both use
        # 45577), so the board folder in the index is the identity.
        'refuseBoards': (('OUTLET_BSEED', 'bseed-ts011f'), ('SWITCH_BSEED_TS0726_3GANG', 'bseed-ts0726')),
        'pads': None,
        'notes': [
            'Find the board name with: boards --search <part of the Zigbee manufacturer name>, '
            'for example boards --search 4rbqgcuv for an AVATTO ZWSM16-1.',
            'The image is the released one listed in the OTA index, checked against its SHA-512.',
            'Stock-to-custom conversion may require reset and pairing under the device-specific '
            'procedure; these are separate authorized actions, never automatic recovery steps.',
            'No-neutral modules (for example AVATTO LZWSM16) run from the adapter 3.3 V while flashing.',
            'Silicon Labs (EFR32) boards cannot be flashed over SWire; prepare refuses them.',
        ],
    },
    'bseed-ts0726': {
        'name': 'BSEED TS0726 3-gang Echo Click / Scale dimmer-switch (iedhxgyi, Tuya ZT3L module)',
        'manufacturerCodes': [0x1141],
        'imageTypes': [45577],
        'index': 'bseed',
        'pads': None,
        'notes': [
            "The image is this fork's released TS0726 build from index_bseed.json, checked against its SHA-512.",
            'Only the 3-gang iedhxgyi board. The 1-, 2- and 4-gang TS0726 use their own images: '
            'use --device tuya-zigbee-switch --board SWITCH_BSEED_TS0726_<n>GANG.',
            'Switch the circuit off at the breaker and take the switch out of the wall box before opening it.',
        ],
    },
    'generic-tlsr8258': {
        'name': 'Other TLSR8258 512 KiB device (no profile)',
        'requiresInputSha256': True,
        'pads': [('SWS', 'the TLSR8258 SWS/PA7 pin'), ('GND', 'ground'), ('3V3', 'the 3.3 V rail')],
        'notes': ['No device-specific checks. The flash layout must match the 512 KiB SDK layout.'],
    },
}
TUYA_MODULE_PADS = [
    ('SWS', 'pad labelled SWS on the Tuya module (ZTU: castellated pin 4; ZT3L: SWS on the module '
            'silkscreen); confirm by continuity'),
    ('GND', 'module GND (ZTU pin 13), or a board GND pad that measures 0 ohm to it'),
    ('3V3', 'module VCC (ZTU pin 14), or the board 3.3 V rail after the regulator'),
]
for _profile in ('tuya-zigbee-switch', 'bseed-ts0726'):
    DEVICES[_profile]['pads'] = TUYA_MODULE_PADS
INDEX_BASE = 'https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/'
INDEX_FILES = {'router': 'index_router.json', 'end_device': 'index_end_device.json', 'bseed': 'index_bseed.json'}
INDEX_LIMIT = 8 * 1024 * 1024
ROLES = ('router', 'end_device')
DEVICES['bseed-ts011f-pm-r19-recovery'] = {
    **DEVICES['bseed-ts011f'], 'name': 'BSEED PM Router r19 historical wired rollback',
    'requiresEvidence': False, 'historicalRecovery': R19_RECOVERY,
    'notes': ['Only the exact finalized r19 payload from the original hosted build is admitted.',
              'Use only as the deliberately selected incident fallback, then stop and record hardware evidence.'],
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
        raise RecoveryError('recognized tuya-zigbee-switch, BSEED or Gledopto identity requires its guarded '
                            'device profile (tuya-zigbee-switch --board, bseed-ts011f, bseed-ts0726 or '
                            'gledopto-gl-sd-301p)')
    if device.get('index'):
        entry = source.get('index')
        if not entry:
            raise RecoveryError(f'{device_id} images come from the OTA index: use --board and --role '
                                '(and --index FILE when offline)')
        for key in ('imageType', 'manufacturerCode', 'fileVersion'):
            if entry.get(key) != info[key]:
                raise RecoveryError(f'OTA index {key} {entry.get(key)} differs from the image ({info[key]})')
        if device['index'] == 'board':
            check_board(device_id, entry.get('board'))
        check_index_source(device_id, info, entry)
    historical = device.get('historicalRecovery')
    if historical:
        if any(info.get(k) != historical[k] for k in ('sha256', 'bytes', 'fileVersion', 'manufacturerCode', 'imageType')):
            raise RecoveryError('historical rollback profile only accepts the exact pinned PM Router r19 image')
        if source.get('historicalRecovery') != historical:
            raise RecoveryError('historical rollback provenance is missing or changed')
        manifest = source.get('manifest')
        if manifest and any(manifest.get(k) != historical[v] for k, v in (
                ('sourceCommit', 'sourceCommit'), ('board', 'board'), ('swBuildId', 'build'))):
            raise RecoveryError('historical rollback manifest contradicts the pinned original source')
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
        raise RecoveryError('Gledopto custom firmware remains quarantined except for the exact pinned '
                            'ED-008 candidate; this profile permits only pinned stock or ED-008 bytes')
    if images is not None and info['sha256'] not in images and not source.get('buildManifest'):
        raise RecoveryError(f'image {info["sha256"][:12]} is not a known {device_id} image and has no '
                            'CI build evidence (SHA256SUMS and manifest.txt next to it)')
    if device.get('requiresInputSha256') and not source.get('pinnedSha256'):
        raise RecoveryError(f'{device_id} needs --expect-sha256 of the input file')
    return device


def check_board(device_id, board):
    if not isinstance(board, str) or not re.fullmatch(r'[A-Z0-9_]+', board):
        raise RecoveryError('--board must be a device_db.yaml name such as MODULE_AVATTO_A_TS0001; '
                            'find it with: boards --search <text>', code='INVALID_ARGUMENT')
    for prefix, other in DEVICES[device_id].get('refuseBoards', ()):
        if board.startswith(prefix):
            raise RecoveryError(f'{board} has its own gated firmware line here; use --device {other}')


def check_index_source(device_id, info, entry):
    """Recheck board/role/URL and native bytes when reopening an indexed bundle.

    An offline index is operator-supplied provenance, not a signed catalogue.
    Refuse internally contradictory evidence; do not infer identity from an
    image type alone because upstream and fork firmware lines reuse them.
    """
    from urllib.parse import urlsplit
    board, role = entry.get('board'), entry.get('role')
    url = urlsplit(str(entry.get('url', '')))
    name = entry.get('fileName')
    if (url.scheme != 'https' or url.netloc not in ('github.com', 'raw.githubusercontent.com')
            or url.query or url.fragment or not isinstance(name, str) or '/' in name
            or url.path.rsplit('/', 1)[-1] != name or 'from_tuya' in name or 'FORCE' in name):
        raise RecoveryError('OTA index source URL/fileName is inconsistent with a native release')
    if DEVICES[device_id]['index'] == 'bseed':
        if board != 'SWITCH_BSEED_TS0726_3GANG' or role != 'router':
            raise RecoveryError('bseed-ts0726 requires SWITCH_BSEED_TS0726_3GANG as router')
        if (url.netloc != 'raw.githubusercontent.com'
                or not re.fullmatch(r'/analienx/tuya-zigbee-switch/[^/]+/zigbee2mqtt/ota/bseed/ts0726-[^/]+', url.path)):
            raise RecoveryError('TS0726 source must be the fork TS0726 release, not another firmware line')
    else:
        if role not in ROLES:
            raise RecoveryError('OTA index source role is invalid')
        folder = str(board) + ('_END_DEVICE' if role == 'end_device' else '')
        match = re.fullmatch(r'/[A-Za-z0-9-]+/tuya-zigbee-switch/(?:raw/)?[0-9a-f]{40}'
                             r'/bin/(router|end_device)/([A-Z0-9_]+)/([^/]+)', url.path)
        if not match or match.group(1, 2) != (role, folder):
            raise RecoveryError('OTA index board/role differs from its source URL')
    if entry.get('nativeSha256') != info['sha256']:
        raise RecoveryError('native image differs from the indexed bundle provenance')


def index_entry(entries, device_id, board, role):
    """The one released image for this board and role in a tuya-zigbee-switch OTA index."""
    if not isinstance(entries, list):
        raise RecoveryError('OTA index is not a list')
    real = [e for e in entries if isinstance(e, dict) and 'from_tuya' not in str(e.get('fileName'))
            and 'FORCE' not in str(e.get('fileName'))]
    if DEVICES[device_id]['index'] == 'bseed':
        found = [e for e in real if e.get('imageType') in DEVICES[device_id]['imageTypes']]
        what = device_id
    else:
        folder = board + ('_END_DEVICE' if role == 'end_device' else '')
        found = [e for e in real if f'/bin/{role}/{folder}/' in str(e.get('url'))]
        what = f'{board} as {role}'
    if len(found) != 1:
        raise RecoveryError(f'the OTA index lists {len(found)} released images for {what}; '
                            'check the board name and role with: boards --search <text>')
    entry = found[0]
    if (not re.fullmatch(r'[0-9a-f]{128}', str(entry.get('sha512')))
            or not isinstance(entry.get('fileSize'), int) or not 0 < entry['fileSize'] <= 0x80000):
        raise RecoveryError('OTA index entry has no usable SHA-512 and size')
    return entry


def board_list(entries, role, search=None):
    """Boards with a released image in an index, optionally filtered by name text."""
    rows = {}
    for entry in entries if isinstance(entries, list) else []:
        match = re.search(r'/bin/([a-z_]+)/([A-Z0-9_]+)/', str(entry.get('url')))
        name = str(entry.get('fileName'))
        if not match or match.group(1) != role or 'from_tuya' in name or 'FORCE' in name:
            continue
        board = match.group(2)
        if role == 'end_device' and board.endswith('_END_DEVICE'):
            board = board[:-len('_END_DEVICE')]
        names = [str(n) for n in entry.get('manufacturerName') or []]
        if search and search.lower() not in ' '.join([board] + names).lower():
            continue
        rows[board] = {'board': board, 'role': role, 'manufacturerNames': names,
                       'imageType': entry.get('imageType'), 'fileVersion': entry.get('fileVersion')}
    return [rows[key] for key in sorted(rows)]


def load_index(name, index_path, fetch):
    if index_path:
        return read_json(index_path)
    data = fetch(INDEX_BASE + INDEX_FILES[name], INDEX_LIMIT)
    if len(data) > INDEX_LIMIT:
        raise RecoveryError('OTA index is larger than expected')
    return json.loads(data.decode('utf-8'))


def image_label(device_id, info, source):
    images = DEVICES[device_id].get('images') or {}
    manifest, matrix = source.get('manifest') or {}, source.get('roleMatrix') or {}
    build = (source.get('buildManifest') or {}).get('DEV_IDENTITY')
    entry = source.get('index') or {}
    released = entry and f'{entry.get("board") or device_id} {entry.get("role")}, {entry.get("fileName")}'
    return (images.get(info['sha256']) or (source.get('historicalRecovery') or {}).get('build')
            or manifest.get('swBuildId') or matrix.get('build') or build or released
            or source.get('inputFile'))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def download(url, limit):
    """Fetch one file; every caller checks a pinned hash before using it."""
    if not (url.startswith('https://raw.githubusercontent.com/')
            or re.match(r'https://github\.com/[A-Za-z0-9-]+/tuya-zigbee-switch/raw/[0-9a-f]{40}/', url)):
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


def load_indexed(device_id, ota_path, image_path, fetch, board, role, index_path):
    """Resolve the released OTA for a board from the index and check it against its SHA-512."""
    device = DEVICES[device_id]
    if image_path:
        raise RecoveryError(f'{device_id} flashes the released OTA file from the index; give --board, not --image')
    if device['index'] == 'bseed':
        if board not in (None, 'SWITCH_BSEED_TS0726_3GANG'):
            raise RecoveryError('--board conflicts with bseed-ts0726: expected SWITCH_BSEED_TS0726_3GANG',
                                code='INVALID_ARGUMENT')
        if role not in (None, 'router'):
            raise RecoveryError(f'{device_id} has a released Router image only')
        role, board, index_name = 'router', 'SWITCH_BSEED_TS0726_3GANG', 'bseed'
    else:
        role = role or 'router'
        if role not in ROLES:
            raise RecoveryError(f'--role must be one of {", ".join(ROLES)}', code='INVALID_ARGUMENT')
        check_board(device_id, board)
        index_name = role
    entry = index_entry(load_index(index_name, index_path, fetch), device_id, board, role)
    if ota_path:
        data, name = Path(ota_path).read_bytes(), Path(ota_path).name
    else:
        data, name = fetch(entry['url'], entry['fileSize']), entry['fileName']
    if len(data) != entry['fileSize'] or hashlib.sha512(data).hexdigest() != entry['sha512']:
        raise RecoveryError(f'{name} does not match the size and SHA-512 listed in the OTA index')
    source = {
        'inputFile': name, 'pinnedSha256': sha256(data),
        'index': {'file': str(index_path) if index_path else INDEX_BASE + INDEX_FILES[index_name],
                  'board': board, 'role': role,
                  **{key: entry.get(key) for key in ('fileName', 'url', 'fileSize', 'sha512', 'imageType',
                                                     'manufacturerCode', 'fileVersion')}},
    }
    if not ota_path:
        source['inputUrl'] = entry['url']
    try:
        image_from_ota(data)
    except RecoveryError as error:
        raise RecoveryError(f'{board}: {error}. Only Telink TLSR825x boards can be flashed over SWire; '
                            'Silicon Labs boards need SWD.') from error
    return data, source


def load_input(device_id, ota_path, image_path, expect_sha256, fetch, *, board=None, role=None,
               index_path=None):
    """Read the OTA file or finalized image and return (image, info, source)."""
    device = DEVICES.get(device_id)
    if device is None:
        raise RecoveryError(f'unknown device {device_id!r}; use one of {", ".join(DEVICES)}')
    if ota_path and image_path:
        raise RecoveryError('give --ota or --image, not both')
    if (board or index_path or role) and not device.get('index'):
        raise RecoveryError('--board, --role and --index apply to tuya-zigbee-switch and bseed-ts0726 only',
                            code='INVALID_ARGUMENT')
    source = {}
    if device.get('index'):
        data, source = load_indexed(device_id, ota_path, image_path, fetch, board, role, index_path)
        kind = 'ota'
    elif not ota_path and not image_path:
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
    if device.get('index'):
        source['index']['nativeSha256'] = info['sha256']
    if device.get('historicalRecovery'):
        source['historicalRecovery'] = device['historicalRecovery']
        if kind == 'ota' and digest != device['historicalRecovery']['otaSha256']:
            raise RecoveryError('historical rollback OTA differs from its pinned original container')
    return image, info, source, data


def prepare(ota_path=None, out=None, *, device='bseed-ts011f', image_path=None, manifest=None, role_matrix=None,
            expect_sha256=None, expect_build=None, fetch=download, expect_ota_sha256=None,
            board=None, role=None, index=None):
    expect_sha256 = expect_sha256 or expect_ota_sha256
    image, info, source, data = load_input(device, ota_path, image_path, expect_sha256, fetch,
                                           board=board, role=role, index_path=index)
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


def wiring(device_id, connection='tx-only'):
    device = DEVICES.get(device_id)
    if device is None:
        raise RecoveryError(f'unknown device {device_id!r}; use one of {", ".join(DEVICES)}')
    lines = [device['name'], '',
             'Connect (board off mains, everything unpowered while you measure):']
    lines += [f'  {("SWS node" if connection == "cp2102-single-sws" else "TXD") if pad == "SWS" else pad} -> {pad} {where}'
              for pad, where in device['pads']]
    if connection == 'cp2102-single-sws':
        lines += ['', 'Bidirectional junction at the ADAPTER, not the target:',
                  '  TXD --[1.2 kOhm]--+-- one lead -- SWS',
                  '  RXD -------------+',
                  '  3V3 -------------------------- 3V3',
                  '  GND -------------------------- GND',
                  'Exactly three target contacts: SWS, 3V3 and GND.',
                  'Use a mechanically secure insulated junction; software cannot measure its contact or resistance.',
                  'RTS, DTR, +5V, RS232 and RS485 stay unconnected.',
                  'CP2102 RX LEDs/input loading can prevent target readback; a chip name is not qualification.']
    else:
        lines += ['  RXD, RTS, DTR and +5V stay unconnected.']
    lines += ['',
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


def open_port(port, timeout=0.5, *, baud=UART_BAUD):
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
        raise RecoveryError('port must look like COM5 or /dev/ttyUSB0')
    try:
        import serial
    except ImportError as error:
        raise RecoveryError('pyserial is required: python -m pip install pyserial') from error
    com = serial.Serial(port=None, baudrate=baud, timeout=timeout, write_timeout=30,
                        rtscts=False, dsrdtr=False, xonxoff=False)
    com.rts = False
    com.dtr = False
    com.port = port
    com.open()
    return com


def selftest(com, seconds=3.0, chunk=512):
    """Loop TXD back to RXD on the adapter alone and compare what comes back.

    Sends in small chunks and reads each back, so the driver's receive
    buffer never has to hold more than one chunk.
    """
    # Adapter only. Do not intentionally send a valid CPU-stop command as the
    # old loopback pattern did. This still is not a target-safe test by itself.
    pattern = bytes(v for v in range(256) if v != 0x80) * 8
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


def benchmark_echo(com, metrics, *, baud, duration=5.0, progress=None, debug=False,
                   clock=time.monotonic):
    """Adapter-only sustained echo test. Rates count verified bytes, not baud.

    Stop at the first transport/data fault. Pattern excludes the SWire start
    symbol 0x80; this is still only for a disconnected target.
    """
    progress = progress or (lambda *_args, **_kwargs: None)
    started = clock()
    pattern = bytes(value for value in range(256) if value != 0x80) * 4
    chunk_size = 512
    latencies = []
    metrics.update(configuredBaud=baud, theoreticalUartBytesPerSecond=baud / 10,
                   requestedDurationSeconds=duration, chunkBytes=chunk_size, chunksVerified=0,
                   uartBytesAccepted=0, receivedBytes=0, verifiedBytes=0, mismatchedBytes=0,
                   missingBytes=0, timeoutCount=0, firstMismatch=None, stoppedBy=None)

    def snapshot():
        elapsed = max(0.0, clock() - started)
        metrics.update(elapsedSeconds=round(elapsed, 6),
                       verifiedEchoBytesPerSecond=round(metrics['verifiedBytes'] / elapsed, 2) if elapsed else 0,
                       chunkRoundTripMs={'min': round(min(latencies) * 1000, 3) if latencies else None,
                                         'mean': round(sum(latencies) / len(latencies) * 1000, 3) if latencies else None,
                                         'max': round(max(latencies) * 1000, 3) if latencies else None})
        return metrics

    com.reset_input_buffer()
    next_update = started
    progress('echo-start', f'Echo stress test: {baud:,} baud, 8N1, {duration:g} s, {chunk_size}-byte chunks',
             baud=baud, durationSeconds=duration)
    try:
        while clock() - started < duration:
            offset = metrics['uartBytesAccepted']
            shift = metrics['chunksVerified'] % 255
            expected = pattern[shift:shift + chunk_size]
            chunk_started = clock()
            written = com.write(expected)
            metrics['uartBytesAccepted'] += written if isinstance(written, int) and 0 <= written <= chunk_size else 0
            if written != chunk_size:
                metrics['stoppedBy'] = 'UART_SHORT_WRITE'
                raise RecoveryError(f'UART accepted {written!r} of {chunk_size} bytes at offset {offset}',
                                    code='UART_SHORT_WRITE', retry_safe=False)
            com.flush()
            received = bytearray()
            response_deadline = clock() + 0.5
            while len(received) < chunk_size and clock() < response_deadline:
                piece = com.read(chunk_size - len(received))
                if len(piece) > chunk_size - len(received):
                    metrics['stoppedBy'] = 'UART_RESPONSE_LENGTH'
                    raise RecoveryError('adapter returned more bytes than requested', code='UART_RESPONSE_LENGTH')
                received.extend(piece)
                metrics['receivedBytes'] += len(piece)
            latency = clock() - chunk_started
            latencies.append(latency)
            errors = [i for i, value in enumerate(received) if value != expected[i]]
            missing = chunk_size - len(received)
            metrics['mismatchedBytes'] += len(errors)
            metrics['missingBytes'] += missing
            metrics['verifiedBytes'] += len(received) - len(errors)
            if errors or missing:
                index = errors[0] if errors else len(received)
                metrics['firstMismatch'] = {'offset': offset + index, 'expectedHex': f'{expected[index]:02x}',
                                            'receivedHex': f'{received[index]:02x}' if index < len(received) else None}
                metrics['timeoutCount'] += int(bool(missing))
                metrics['stoppedBy'] = 'ADAPTER_ECHO_TIMEOUT' if missing else 'ADAPTER_ECHO_MISMATCH'
                raise RecoveryError(f'Echo failed at byte {offset + index}: {len(errors)} mismatched, '
                                    f'{missing} missing; check the resistor/RX junction and baud',
                                    code=metrics['stoppedBy'], retry_safe=False)
            metrics['chunksVerified'] += 1
            now = clock()
            if debug or now >= next_update:
                current = snapshot()
                progress('echo-progress', f'{current["elapsedSeconds"]:.2f} s: '
                         f'{current["verifiedBytes"]:,} verified bytes, '
                         f'{current["verifiedEchoBytesPerSecond"]:,.0f} B/s, '
                         f'chunk {latency * 1000:.2f} ms, 0 errors',
                         verifiedBytes=current['verifiedBytes'], bytesPerSecond=current['verifiedEchoBytesPerSecond'],
                         chunkRoundTripMs=round(latency * 1000, 3))
                next_update = now + 0.5
        if not metrics['chunksVerified']:
            raise RecoveryError('no echo chunks were verified', code='ADAPTER_NO_DATA')
        metrics['stoppedBy'] = 'DURATION_COMPLETE'
    except BaseException as error:
        if metrics['stoppedBy'] is None:
            metrics['stoppedBy'] = getattr(error, 'code', 'ADAPTER_INTERRUPTED' if isinstance(error, KeyboardInterrupt)
                                           else 'ADAPTER_IO_FAILED')
        raise
    finally:
        snapshot()
        progress('echo-summary', f'Echo result: {metrics["verifiedBytes"]:,}/{metrics["uartBytesAccepted"]:,} bytes verified '
                 f'in {metrics["elapsedSeconds"]:.2f} s, {metrics["verifiedEchoBytesPerSecond"]:,.0f} B/s; '
                 f'{metrics["mismatchedBytes"]} mismatched, {metrics["missingBytes"]} missing, '
                 f'{metrics["timeoutCount"]} timeouts; chunk mean '
                 f'{metrics["chunkRoundTripMs"]["mean"]} ms', **metrics)


def diagnostic_read_header(address):
    """pvvx read-by-address header, without data start or stop words."""
    header = bytearray(swire_frame(address, b'\0')[:50])
    header[41] = 0x80  # read flag 0x80 instead of write flag 0x00
    return bytes(header)


def diagnostic_decode(word):
    """pvvx response sampling at 460800/921600 baud (bit8mask=0x20)."""
    if len(word) != 9 or word[8] & 0xFE != 0xFE:
        raise RecoveryError('invalid SWire response word', code='SWIRE_FRAMING', retry_safe=False)
    value = 0
    for index in range(8):
        value = (value << 1) | int(not (word[index] & (0x20 if index == 0 else 0x10)))
    return value


class SwireDiagnostic:
    """Bounded bidirectional diagnostic; no reset, halt, flash unlock or write.

    Only the SWS divider and read-side SPI/FIFO registers are writable. SPI
    access additionally requires a fresh observed CPU stop value. All flash
    addresses/lengths are fixed to the two 32-byte application-slot headers.
    """
    def __init__(self, port, baud, *, clock=time.monotonic, sleep=time.sleep, response_timeout=0.15):
        self.port, self.baud, self.clock, self.sleep = port, baud, clock, sleep
        self.response_timeout = response_timeout
        self.deadline = clock() + 25
        self.flash_access_safe = False
        self.progress = lambda *_args, **_kwargs: None
        self.debug = False
        self.stats = {'uartBytesSent': 0, 'echoBytesVerified': 0, 'targetBytesDecoded': 0, 'dividerAttempts': 0,
                      'targetReadTimeouts': 0, 'echoOnlyReads': 0, 'lastTargetResponse': None}

    def read_exact(self, size):
        deadline = min(self.deadline, self.clock() + self.response_timeout)
        result = bytearray()
        while len(result) < size and self.clock() < deadline:
            piece = self.port.read(size - len(result))
            if len(piece) > size - len(result):
                raise RecoveryError('unexpected extra serial data', code='UART_RESPONSE_LENGTH', retry_safe=False)
            result.extend(piece)
        if len(result) != size:
            raise RecoveryError('missing or short serial response', code='SWIRE_RESPONSE_TIMEOUT', retry_safe=False,
                                details={'expectedBytes': size, 'receivedBytes': len(result)})
        return bytes(result)

    def transmit(self, data):
        if self.clock() >= self.deadline:
            raise RecoveryError('SWire diagnostic deadline exceeded', code='LINK_TEST_TIMEOUT', retry_safe=False)
        # pvvx keeps USB packets a multiple of ten UART bytes, at most 60.
        for offset in range(0, len(data), 60):
            piece = data[offset:offset + 60]
            if self.port.write(piece) != len(piece):
                raise RecoveryError('short UART write', code='UART_SHORT_WRITE', retry_safe=False)
            self.port.flush()
            self.stats['uartBytesSent'] += len(piece)

    def echo(self, data):
        self.transmit(data)
        if self.read_exact(len(data)) != data:
            raise RecoveryError('UART echo differs; check the RX junction, TTL mode and input loading',
                                code='UART_ECHO_MISMATCH', retry_safe=False)
        self.stats['echoBytesVerified'] += len(data)

    def write_register(self, address, data):
        data = bytes(data)
        safe = address == 0xB2 and len(data) == 1 and 1 <= data[0] <= 127
        if self.flash_access_safe:
            safe |= address == 0xB3 and data in (b'\x80', b'\0')
            safe |= address == 0x0D and data in (b'\0', b'\x0a', b'\x01')
            safe |= (address == 0x0C and data in
                     (b'\x03\x00\x00\x00\x00', b'\x03\x04\x00\x00\x00'))
        if not safe:
            raise RecoveryError('diagnostic register write is outside its read-only-flash allowlist',
                                code='DIAGNOSTIC_WRITE_FORBIDDEN')
        self.port.reset_input_buffer()
        self.echo(swire_frame(address, data))

    def read_register(self, address, size=1):
        if not ((address in (0xB2, 0x602) and size == 1)
                or (self.flash_access_safe and address == 0x0C and size == 32)):
            raise RecoveryError('diagnostic register read is outside its allowlist', code='DIAGNOSTIC_READ_FORBIDDEN')
        self.sleep(0.05)  # same settle interval as the pinned pvvx reader
        self.port.reset_input_buffer()
        self.echo(diagnostic_read_header(address))
        result = bytearray()
        serial_failed = False
        try:
            for _ in range(size):
                self.transmit(b'\xfe')
                try:
                    samples = self.read_exact(9)
                except RecoveryError as error:
                    if error.code == 'SWIRE_RESPONSE_TIMEOUT':
                        self.stats['targetReadTimeouts'] += 1
                        self.stats['lastTargetResponse'] = dict(error.details)
                        self.stats['echoOnlyReads'] += int(error.details.get('receivedBytes') == 1)
                    raise
                result.append(diagnostic_decode(samples))
                self.stats['targetBytesDecoded'] += 1
        except OSError:
            serial_failed = True
            self.stats['cleanupSkippedAfterSerialFailure'] = True
            raise
        finally:
            if not serial_failed:
                self.sleep(0.01)
                self.port.reset_input_buffer()
                self.echo(b'\x80' * 9 + b'\xfe')  # terminate the SWire read; never a flash write
        return bytes(result)

    def find_divider(self):
        low = round(16_000_000 * 2 / self.baud)
        high = min(127, round(48_000_000 * 2 / self.baud))
        self.progress('divider-scan', f'Scanning SWire divider {low}..{high}; UART echo alone does not count',
                      minimum=low, maximum=high)
        for divider in range(low, high + 1):
            self.stats['dividerAttempts'] += 1
            if self.debug or self.stats['dividerAttempts'] == 1 or self.stats['dividerAttempts'] % 8 == 0:
                self.progress('divider-attempt', f'Trying divider {divider} ({self.stats["dividerAttempts"]}/{high - low + 1})',
                              divider=divider, attempt=self.stats['dividerAttempts'])
            self.write_register(0xB2, bytes([divider]))
            try:
                result = self.read_register(0xB2)
            except RecoveryError as error:
                if error.code in ('SWIRE_RESPONSE_TIMEOUT', 'SWIRE_FRAMING'):
                    if self.debug:
                        counts = error.details
                        received, expected = counts.get('receivedBytes'), counts.get('expectedBytes')
                        detail = (f'; {received}/{expected} UART samples'
                                  + (' (adapter echo only)' if received == 1 and expected == 9 else '')) if counts else ''
                        self.progress('divider-no-reply', f'Divider {divider}: {error.code}{detail}',
                                      divider=divider, errorCode=error.code, **counts)
                    continue
                raise
            if result == bytes([divider]):
                # Echo alone never supplies a nine-byte target response. Require
                # repeated exact register values, not merely a received byte count.
                for _ in range(2):
                    if self.read_register(0xB2) != result:
                        raise RecoveryError('unstable target register readback', code='UNSTABLE_SWIRE_READBACK', retry_safe=False)
                self.progress('divider-found', f'Divider {divider}: three matching target register replies', divider=divider)
                return divider
        raise RecoveryError('adapter echo is present but no consistent target SWire response was found',
                            code='NO_SWIRE_TARGET_RESPONSE', retry_safe=False)

    def flash_header(self, address):
        if not self.flash_access_safe or address not in (0, 0x40000):
            raise RecoveryError('header probe requires a halted CPU and a known application slot', code='DIAGNOSTIC_READ_FORBIDDEN')
        if self.read_register(0x602)[0] != 0x05:
            self.flash_access_safe = False
            raise RecoveryError('CPU no longer reports halted; flash probe stopped', code='TARGET_STATE_CHANGED', retry_safe=False)
        try:
            self.write_register(0xB3, b'\x80')
            self.write_register(0x0D, b'\0')
            self.write_register(0x0C, bytes((0x03, address >> 16, (address >> 8) & 255, address & 255, 0)))
            self.write_register(0x0D, b'\x0a')
            return self.read_register(0x0C, 32)
        finally:
            try:
                self.write_register(0x0D, b'\x01')
            finally:
                self.write_register(0xB3, b'\0')


def connection_spec(kind):
    if kind == 'cp2102-single-sws':
        return {'id': kind, 'adapter': 'CP2102 (operator-declared)', 'resistorNominalOhms': 1200,
                'upstreamResistorRangeOhms': [1000, 1800], 'targetContacts': ['SWS', '3V3', 'GND'],
                'junction': 'adapter-side: TXD through resistor to RXD and one SWS lead',
                'unused': ['+5V', 'RTS', 'DTR', 'RS232', 'RS485'], 'electricallyVerified': False}
    return {'id': 'tx-only', 'targetContacts': ['SWS', '3V3', 'GND'], 'rx': 'unused'}


def test_link(port, output, *, phase, device=None, baud=921600, require_flash_read=False,
              duration=None, debug=False, out=None, opener=None, probe_factory=SwireDiagnostic,
              benchmark=None):
    if phase not in ('adapter', 'target') or baud not in (460800, 921600):
        raise RecoveryError('invalid link test phase or baud', code='INVALID_ARGUMENT')
    if phase == 'target' and device not in DEVICES:
        raise RecoveryError('target testing needs an explicit supported --device profile', code='INVALID_ARGUMENT')
    if require_flash_read and phase != 'target':
        raise RecoveryError('--require-flash-read is only valid for the target phase', code='INVALID_ARGUMENT')
    if duration is not None and (phase != 'adapter' or not math.isfinite(duration) or not 0.1 <= duration <= 30):
        raise RecoveryError('--duration must be 0.1..30 seconds and is adapter-only', code='INVALID_ARGUMENT')
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
        raise RecoveryError('invalid serial port', code='INVALID_ARGUMENT')
    output = private_output(output)
    if output.exists():
        raise RecoveryError('diagnostic receipt already exists; choose a new path', code='EVIDENCE_EXISTS')
    started = time.monotonic()
    result = {'status': 'STARTED', 'phase': phase, 'port': port, 'baud': baud, 'deviceProfile': device,
              'connection': connection_spec('cp2102-single-sws'), 'utc': utc_now().isoformat(),
              'flashWriteAttempted': False, 'cpuResetRequested': False, 'cpuHaltRequested': False,
              'volatileConfigurationMayHaveChanged': phase == 'target', 'flashWriteTested': False,
              'targetReplyVerified': False, 'fullBackupVerified': False, 'bootVerified': False,
              'receipt': str(output), 'electricalMeasurementsVerified': False, 'events': []}

    def progress(event, message, **details):
        elapsed = round(time.monotonic() - started, 6)
        result['events'].append({'event': event, 'elapsedSeconds': elapsed, 'message': message, 'details': details})
        # Bound private debug receipts, including per-chunk --debug output.
        if len(result['events']) > 200:
            result['events'].pop(1)
        if out:
            out(f'[{elapsed:6.2f}s] {message}')

    with PortGuard(port):
        authority = authority_path(port)
        if phase == 'adapter' and authority.exists() and read_json(authority)['phase'] != 'CLOSED':
            raise RecoveryError('adapter-only echo testing is forbidden during an active target session', code='INVALID_PHASE')
        # Port locks do not serialize a second port choosing the same output.
        # Reserve the receipt exclusively before touching either target.
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise RecoveryError('diagnostic receipt already exists; choose a new path', code='EVIDENCE_EXISTS') from error
        probe = None
        failure_stage = 'serial-open'
        try:
            progress('port-open', f'Opening {port}: {baud:,} baud, 8N1; phase={phase}', port=port, baud=baud)
            with (opener or (lambda p: open_port(p, timeout=0.02, baud=baud)))(port) as com:
                failure_stage = 'serial-probe'
                if phase == 'adapter':
                    metrics = result.setdefault('statistics', {})
                    (benchmark or benchmark_echo)(com, metrics, baud=baud, duration=duration if duration is not None else 5.0,
                                                  progress=progress, debug=debug)
                    result.update(receivedBytes=metrics['receivedBytes'], sentBytes=metrics['uartBytesAccepted'])
                    result.update(status='ADAPTER_ECHO_OK', proofLevel='adapter-echo-only',
                                  nextActions=['measure TTL/supply and qualify contacts', 'authorized target-phase test'])
                else:
                    probe = probe_factory(com, baud)
                    probe.progress, probe.debug = progress, debug
                    result['divider'] = probe.find_divider()
                    result['targetReplyVerified'] = True
                    controls = [probe.read_register(0x602)[0] for _ in range(2)]
                    if controls[0] != controls[1]:
                        raise RecoveryError('CPU control reads are unstable', code='UNSTABLE_SWIRE_READBACK')
                    stopped = controls[0] == 0x05
                    progress('cpu-control', f'CPU control: 0x{controls[0]:02x} twice; '
                             + ('already halted' if stopped else 'not known halted; flash probe skipped'),
                             value=controls[0], halted=stopped)
                    result.update(cpuControl=controls[0], cpuObservedHalted=stopped,
                                  status='SWIRE_RESPONSE_OK', proofLevel='target-register-readback')
                    if not stopped:
                        result.update(flashReadProbe='SKIPPED_CPU_NOT_HALTED',
                                      nextActions=['qualified halt/entry procedure before flash reads; never automatic'])
                        if require_flash_read:
                            raise RecoveryError('target replies, but CPU is not halted; flash probe was not attempted', code='TARGET_NOT_HALTED')
                    else:
                        probe.flash_access_safe = True
                        headers = []
                        for name, address in SLOTS.items():
                            progress('header-read', f'Slot {name} at 0x{address:05x}: reading 32 bytes twice', slot=name)
                            first, second = probe.flash_header(address), probe.flash_header(address)
                            if first != second:
                                raise RecoveryError('repeated application-header reads differ', code='UNSTABLE_FLASH_READ')
                            headers.append({'slot': name, 'bytes': 32, 'sha256': sha256(first),
                                            'markerHex': first[8:12].hex(), 'erased': first == b'\xff' * 32})
                            progress('header-matched', f'Slot {name}: both reads match, marker={first[8:12].hex()}, '
                                     f'SHA-256={sha256(first)[:16]}...', slot=name, sha256=sha256(first))
                        result.update(status='FLASH_READ_PROBE_OK', proofLevel='registers-and-small-flash-reads',
                                      flashReadProbe='MATCHED_SLOT_HEADERS', headers=headers,
                                      nextActions=['full matching double read before verified recovery'])
                    result['statistics'] = probe.stats
            return result
        except BaseException as error:
            code = getattr(error, 'code', None) or ('LINK_TEST_INTERRUPTED' if isinstance(error, KeyboardInterrupt) else (
                ('SERIAL_OPEN_FAILED' if failure_stage == 'serial-open' else 'SERIAL_IO_FAILED')
                if isinstance(error, OSError) else 'LINK_TEST_IO_FAILED'))
            result.update(status='LINK_TEST_FAILED', errorCode=code, error=str(error), failureStage=failure_stage,
                          errorDetails={'exceptionType': type(error).__name__,
                                        'errno': getattr(error, 'errno', None), 'winerror': getattr(error, 'winerror', None),
                                        **getattr(error, 'details', {})})
            progress('failure', f'{result["errorCode"]}: {error}', errorCode=result['errorCode'])
            raise RecoveryError(str(error), code=result['errorCode'], retry_safe=False,
                                side_effects=phase == 'target', details={'receipt': str(output),
                                'flashWriteAttempted': False, 'targetReplyVerified': result['targetReplyVerified'],
                                'statistics': probe.stats if probe is not None else result.get('statistics', {})},
                                exit_code=130 if isinstance(error, KeyboardInterrupt) else 2) from error
        finally:
            result['elapsedSeconds'] = round(time.monotonic() - started, 6)
            if probe is not None:
                probe.stats['elapsedSeconds'] = result['elapsedSeconds']
                probe.stats['targetBytesPerSecond'] = round(probe.stats['targetBytesDecoded'] / max(result['elapsedSeconds'], 1e-9), 2)
                result['statistics'] = probe.stats
                progress('target-summary', f'{probe.stats["targetBytesDecoded"]} target bytes decoded; '
                         f'{probe.stats["echoBytesVerified"]:,} echo bytes checked; '
                         f'{probe.stats["dividerAttempts"]} divider attempts; '
                         f'{result["elapsedSeconds"]:.2f} s total (diagnostic rate, not firmware speed)', **probe.stats)
            atomic_json(output, result)


def adapter_identity(row):
    """USB adapter identity, never proof of the attached Zigbee board."""
    fields = {key: row.get(key) for key in ('vid', 'pid', 'serialNumber', 'location')}
    if fields['vid'] is not None and fields['pid'] is not None and (fields['serialNumber'] or fields['location']):
        return fields
    return {'port': port_key(row['device']), 'hwid': row.get('hwid')}


def watch_link(port, output, *, device, seconds=180, baud=921600, debug=False, out=None,
               inventory=None, probe=None, clock=time.monotonic, sleep=time.sleep):
    """Bounded target diagnostics with USB readiness checks and fault backoff.

    No adapter-only patterns, reset/halt, full read, recovery write or unlock.
    Each test_link retains its normal port lock and immutable private receipt.
    """
    if device not in DEVICES or not isinstance(seconds, int) or not 1 <= seconds <= 600 or baud not in (460800, 921600):
        raise RecoveryError('watch needs a supported device, baud and 1..600 seconds', code='INVALID_ARGUMENT')
    if not re.fullmatch(r'(?:COM\d+|/dev/[A-Za-z0-9_./-]+)', port or '', re.IGNORECASE):
        raise RecoveryError('invalid serial port', code='INVALID_ARGUMENT')
    output = private_output(output)
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise RecoveryError('watch output directory must be new', code='EVIDENCE_EXISTS') from error
    inventory, probe = inventory or list_ports, probe or test_link
    started, bound, fault_streak, not_before = clock(), None, 0, 0.0
    summary_path = output / 'summary.json'
    result = {'status': 'WATCH_STARTED', 'requestedPort': port, 'port': port, 'deviceProfile': device,
              'seconds': seconds, 'baud': baud, 'summary': str(summary_path), 'adapterIdentity': None,
              'attempts': [], 'events': [], 'targetReplyVerified': False, 'flashWriteAttempted': False,
              'cpuResetRequested': False, 'cpuHaltRequested': False, 'bootVerified': False,
              'nextActions': ['wait for the selected USB adapter and qualified target setup']}
    last_notice, last_notice_at = None, -10.0

    def report(state, message, **details):
        nonlocal last_notice, last_notice_at
        elapsed = clock() - started
        if state != last_notice or elapsed - last_notice_at >= 5:
            result['events'].append({'state': state, 'elapsedSeconds': round(elapsed, 3), 'message': message, **details})
            result['events'] = result['events'][-200:]
            if out:
                out(f'[{elapsed:6.1f}/{seconds}s] {message}')
            last_notice, last_notice_at = state, elapsed
        result['state'] = state
        result['elapsedSeconds'] = round(elapsed, 3)
        atomic_json(summary_path, result)

    report('WAITING_FOR_USB', f'Waiting for {port}; target tests only, no reset/halt/flash. Ctrl+C stops.')
    try:
        while clock() - started < seconds:
            rows = inventory()
            if bound is None:
                candidates = [row for row in rows if row.get('device') and port_key(row['device']) == port_key(port)]
            else:
                candidates = [row for row in rows if row.get('device') and adapter_identity(row) == bound]
            if len(candidates) > 1:
                result['status'] = 'WATCH_BLOCKED'
                report('ADAPTER_AMBIGUOUS', 'Multiple ports match the selected adapter; no port will be opened.')
                break
            if not candidates:
                report('WAITING_FOR_USB', 'Selected USB adapter is absent. Waiting for reconnect; no SWire traffic.')
                sleep(min(1, max(0, seconds - (clock() - started))))
                continue
            selected = candidates[0]
            if bound is None:
                bound = adapter_identity(selected)
                result['adapterIdentity'] = bound
                report('ADAPTER_IDENTIFIED', f'Adapter identified on {selected["device"]}; identity refers to USB, not the target.')
            result['port'] = selected['device']
            if clock() < not_before:
                report('WAITING_FOR_USB_READY' if fault_streak else 'WAITING_FOR_TARGET',
                       'USB/driver failure: waiting before a bounded reopen. Reconnect the adapter if needed.'
                       if fault_streak else 'Waiting briefly before the next target probe.',
                       retryInSeconds=round(not_before - clock(), 1))
                sleep(max(0, min(1, not_before - clock(), seconds - (clock() - started))))
                continue
            if clock() - started >= seconds:
                continue
            receipt = output / f'attempt-{len(result["attempts"]) + 1:03d}.json'
            result['activeReceipt'] = str(receipt)
            report('PROBING_TARGET', f'Target probe {len(result["attempts"]) + 1} on {result["port"]}; '
                   f'{seconds - (clock() - started):.0f}s left. Current bounded probe may finish after the window.')
            try:
                attempt = probe(result['port'], receipt, phase='target', device=device, baud=baud,
                                debug=debug, out=out)
            except RecoveryError as error:
                attempt = read_json(receipt) if receipt.exists() else {'errorCode': error.code, 'error': str(error)}
                attempt.setdefault('errorCode', error.code)
            entry = {key: attempt.get(key) for key in ('status', 'errorCode', 'error', 'failureStage', 'statistics')}
            entry.update(receipt=str(receipt), port=result['port'])
            result['attempts'].append(entry)
            if attempt.get('status') in ('SWIRE_RESPONSE_OK', 'FLASH_READ_PROBE_OK') and attempt.get('targetReplyVerified'):
                result.update(status='TARGET_REPLIES_VERIFIED', targetReplyVerified=True,
                              proofLevel=attempt.get('proofLevel'), successfulReceipt=str(receipt),
                              nextActions=attempt.get('nextActions', []))
                report('TARGET_REPLIES_VERIFIED', 'Verified target replies found; watch stopped. Boot remains unverified.')
                break
            code = attempt.get('errorCode')
            if code == 'LINK_TEST_INTERRUPTED':
                result['status'] = 'WATCH_INTERRUPTED'
                report('WATCH_INTERRUPTED', 'Stopped by operator; no further probe will start.')
                break
            if code in ('SERIAL_OPEN_FAILED', 'SERIAL_IO_FAILED'):
                fault_streak += 1
                not_before = clock() + min(10, 2 ** min(fault_streak, 4))
                report('WAITING_FOR_USB_READY', 'USB/driver failed, not a SWire timing result. Waiting for recovery.',
                       errorCode=code, retryInSeconds=not_before - clock())
            elif code in ('NO_SWIRE_TARGET_RESPONSE', 'SWIRE_RESPONSE_TIMEOUT', 'SWIRE_FRAMING',
                          'UART_ECHO_MISMATCH', 'UNSTABLE_SWIRE_READBACK', 'UNSTABLE_FLASH_READ', 'LINK_TEST_TIMEOUT'):
                fault_streak, not_before = 0, clock() + 1
                report('NO_VERIFIED_TARGET_REPLY', 'No verified target reply yet. Adapter echo does not prove SWS contact or SWire entry.',
                       errorCode=code)
            else:
                result['status'] = 'WATCH_BLOCKED'
                report('WATCH_BLOCKED', f'Probe stopped: {code or "unclassified failure"}. No automatic unlock or write.')
                break
        else:
            result['status'] = 'WATCH_TIMEOUT'
            result['nextActions'] = ['inspect private receipts and USB readiness',
                                     'qualify SWS contact, supply and entry state; never automatically reset or flash']
            report('WATCH_TIMEOUT', 'Retry window ended without verified target replies.')
    except KeyboardInterrupt:
        result['status'] = 'WATCH_INTERRUPTED'
        report('WATCH_INTERRUPTED', 'Stopped by operator; inspect the last probe receipt before resuming.')
    except Exception as error:
        result.update(status='WATCH_FAILED', error=str(error))
        report('WATCH_FAILED', f'Watch stopped: {error}; private evidence retained.')
        raise RecoveryError(str(error), code='WATCH_FAILED', retry_safe=False,
                            details={'summary': str(summary_path)}) from error
    finally:
        result['elapsedSeconds'] = round(clock() - started, 3)
        atomic_json(summary_path, result)
    return result


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
            # Double reads can take longer than the unverified stage window.
            # Fresh matching evidence supersedes the old transmission timestamp;
            # replaying a cached verification does not change updatedUtc.
            fresh_since = state['updatedUtc'] if state['phase'] == 'VERIFIED' else state['stageUtc']
            age = (now() - datetime.datetime.fromisoformat(fresh_since)).total_seconds()
            if not 0 <= age <= STAGE_FRESH_SECONDS:
                raise RecoveryError('staged/verified evidence is no longer fresh; read and reconcile before activation', code='STAGE_EXPIRED')
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
    return [{'device': p.device, 'description': p.description, 'hwid': p.hwid,
             'vid': p.vid, 'pid': p.pid, 'serialNumber': p.serial_number, 'location': p.location}
            for p in sorted(ports.comports(), key=lambda p: p.device)]


READER_COMMIT = '042613b1b47a1598856b0ff3e8a133f2918798a5'
READER_SHA256 = '2ea1b7c5266821e5d384a263daa7916bb4020fcdfb8f1ec0bed5842dcb94ca18'
READER_URL = ('https://raw.githubusercontent.com/pvvx/TlsrComSwireWriter/'
              + READER_COMMIT + '/TLSR825xComFlasher.py')
FLASH_BYTES = 0x80000


def ensure_reader(reader=None, *, fetch=download, out=None):
    """Fetch only the required pinned pvvx source; verify before every use.

    A verified shared cache allows subsequent reads to work offline. Explicit
    reader files are checked in place and are never replaced or executed here.
    """
    explicit = reader is not None
    path = Path(reader).resolve() if explicit else private_output(
        STATE_ROOT / 'tools' / 'pvvx' / READER_COMMIT / 'TLSR825xComFlasher.py')
    report = out or (lambda _: None)
    cache_status = 'LOCAL' if explicit else 'CACHED'

    def verified():
        if path.stat().st_size > 256000 or sha256(path.read_bytes()) != READER_SHA256:
            raise RecoveryError('reader must match the pinned pvvx source SHA-256; no serial access attempted',
                                code='READER_HASH_MISMATCH', details={'reader': str(path), 'sha256': READER_SHA256})

    if path.exists():
        verified()
        report(f'pvvx reader: verified {cache_status.lower()} source {READER_COMMIT[:12]} ({path})')
    elif explicit:
        raise RecoveryError(f'explicit reader does not exist: {path}', code='READER_NOT_FOUND')
    else:
        report(f'Downloading pinned pvvx reader {READER_COMMIT[:12]}; verifying SHA-256 before caching')
        started = time.monotonic()
        try:
            data = fetch(READER_URL, 256000)
        except Exception as error:
            raise RecoveryError(f'pvvx reader download failed: {error}; retry online or supply --reader PATH',
                                code='READER_DOWNLOAD_FAILED', retry_safe=True,
                                details={'url': READER_URL, 'reader': str(path)}) from error
        if len(data) > 256000 or sha256(data) != READER_SHA256:
            raise RecoveryError('downloaded reader SHA-256 mismatch; nothing cached or executed', code='READER_HASH_MISMATCH')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # All normal concurrent publishers have verified the exact same
            # bytes. Never execute a partial download, even after interruption.
            if path.exists():
                verified()
            else:
                os.replace(temporary, path)
                cache_status = 'DOWNLOADED'
        finally:
            temporary.unlink(missing_ok=True)
        verified()
        report(f'pvvx reader ready: {len(data):,} bytes, SHA-256 verified, '
               f'{time.monotonic() - started:.2f} s; cache={path}')
    return {'status': 'READER_READY', 'reader': str(path), 'readerCommit': READER_COMMIT,
            'readerSha256': READER_SHA256, 'readerUrl': READER_URL, 'cacheStatus': cache_status,
            'bytes': path.stat().st_size, 'hardwareAccessed': False}


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
               halt=False, fetch=download, runner=subprocess.run, out=None):
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
    reader_setup = ensure_reader(reader, fetch=fetch, out=out)
    reader = Path(reader_setup['reader'])
    result = {'status': 'STARTED', 'address': address, 'bytes': size,
              'port': port, 'readerCommit': READER_COMMIT, 'readerSetup': reader_setup, 'halt': halt,
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
    link = commands.add_parser('test-link', parents=[common], help='test adapter-side resistor/RX/SWS circuit without flashing')
    link.add_argument('--port', required=True)
    link.add_argument('--out', type=Path, required=True, help='new private diagnostic receipt outside Git')
    link.add_argument('--phase', choices=['adapter', 'target'], required=True,
                      help='adapter: target disconnected; target: real SWire responses, no reset/halt/flash write')
    link.add_argument('--device', choices=sorted(DEVICES), help='required for target phase')
    link.add_argument('--baud', type=int, choices=[460800, 921600], default=921600)
    link.add_argument('--duration', type=float, help='adapter-only stress duration, 0.1..30 seconds (default 5)')
    link.add_argument('--debug', action='store_true', help='show every echo chunk or target divider attempt')
    link.add_argument('--require-flash-read', action='store_true', help='fail if CPU is not already halted; never halt it automatically')
    watch = commands.add_parser('watch-link', parents=[common], help='bounded target read diagnostics with USB reconnect/backoff')
    watch.add_argument('--port', required=True)
    watch.add_argument('--device', choices=sorted(DEVICES), required=True)
    watch.add_argument('--out', type=Path, required=True, help='new private directory for summary and attempt receipts')
    watch.add_argument('--seconds', type=int, default=180, help='retry window, 1..600 seconds (default 180)')
    watch.add_argument('--baud', type=int, choices=[460800, 921600], default=921600)
    watch.add_argument('--debug', action='store_true', help='show each divider and sample-count detail')
    setup_reader = commands.add_parser('fetch-reader', parents=[common], help='prefetch/verify the pinned pvvx reader; no serial access')
    setup_reader.add_argument('--reader', type=Path, help='verify an existing offline reader instead of downloading')
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
    wire.add_argument('--connection', choices=['tx-only', 'cp2102-single-sws'], default='tx-only')
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
    prep.add_argument('--board', help='tuya-zigbee-switch: device_db.yaml name, for example MODULE_AVATTO_A_TS0001')
    prep.add_argument('--role', choices=ROLES, help='tuya-zigbee-switch: router (default) or end_device')
    prep.add_argument('--index', type=Path, help='local copy of the OTA index JSON (default: download from main)')
    boards = commands.add_parser('boards', parents=[common],
                                 help='find a tuya-zigbee-switch board name by Zigbee manufacturer name or text')
    boards.add_argument('--search', help='text in the board or Zigbee manufacturer name, for example 4rbqgcuv')
    boards.add_argument('--role', choices=ROLES, default='router')
    boards.add_argument('--index', type=Path, help='local copy of the OTA index JSON (default: download from main)')
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
            result = {'status': 'OK', 'commands': ['prepare', 'boards', 'plan', 'ports', 'wiring', 'selftest', 'test-link', 'watch-link', 'fetch-reader',
                       'stage', 'read', 'inspect', 'verify', 'activate', 'flash', 'status', 'reconcile', 'close', 'unlock'],
                      'jsonNeverPrompts': True, 'idempotentWrites': 'same session + operation ID + exact request',
                      'readOptional': True, 'baselineRequiresVerification': True,
                      'diagnostics': {'adapterEcho': 'not proof of target connection',
                                      'adapterStressSeconds': {'default': 5, 'minimum': 0.1, 'maximum': 30},
                                      'liveProgress': 'human progress on stderr in JSON mode; bounded events in receipt',
                                      'speed': 'verified echo bytes/s and chunk turnaround; not flash throughput',
                                      'targetProbe': 'register readback; small flash reads only if CPU is already halted',
                                      'watch': 'target only; USB identity-bound reconnect, bounded backoff, no reset/halt/flash',
                                      'connection': connection_spec('cp2102-single-sws')},
                      'pvvxReader': {'autoDownload': 'on first read or fetch-reader', 'commit': READER_COMMIT,
                                     'sha256': READER_SHA256, 'offlineCache': True},
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
        elif action == 'test-link':
            result = test_link(args.port, args.out, phase=args.phase, device=args.device, baud=args.baud,
                               require_flash_read=args.require_flash_read, duration=args.duration, debug=args.debug, out=out)
            out(f'Link test: {result["status"]}; proof={result["proofLevel"]}; receipt={args.out}')
        elif action == 'watch-link':
            result = watch_link(args.port, args.out, device=args.device, seconds=args.seconds,
                                baud=args.baud, debug=args.debug, out=out)
            out(f'Watch: {result["status"]}; summary={result["summary"]}')
            emit(result)
            return 0 if result['targetReplyVerified'] else (130 if result['status'] == 'WATCH_INTERRUPTED' else 2)
        elif action == 'fetch-reader':
            result = ensure_reader(args.reader, out=out)
        elif action == 'inspect':
            result = inspect_slots(args.dump, args.reference, replace=args.replace, expect_other=args.expect_other)
            for slot in result['slots']:
                out(f'Slot {slot["slot"]}: {slot["markerState"]}, valid={slot["validImage"]}, matches={slot["matches"]}')
            out(f'Replacement slot: {result["replaceSlot"] or "not established"}')
        elif action == 'read':
            result = read_flash(args.port, args.out, address=args.address, size=args.size,
                                reader=args.reader, halt=args.halt, out=out)
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
                out(wiring(device_id, args.connection) if args.device else f'{device_id}: {DEVICES[device_id]["name"]}')
            result = {'status': 'OK', 'devices': {i: DEVICES[i] for i in ids}, 'connection': connection_spec(args.connection)}
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
                           expect_sha256=args.expect_sha256, expect_build=args.expect_build,
                           board=args.board, role=args.role, index=args.index)
            out(describe(plan))
            result = {'status': 'OK', 'bundle': str(args.out), 'plan': plan}
        elif action == 'boards':
            found = board_list(load_index(args.role, args.index, download), args.role, args.search)
            for row in found:
                out(f'{row["board"]:<42} {", ".join(row["manufacturerNames"][:3])}')
            if not found:
                out('No board matches. Try part of the Zigbee manufacturer name, or --role end_device.')
            else:
                out(f'Then: prepare --device tuya-zigbee-switch --board <name> --role {args.role} --out <folder>')
            result = {'status': 'OK', 'role': args.role, 'boards': found}
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
