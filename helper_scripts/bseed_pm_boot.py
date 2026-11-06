"""Run the exact PM OTA payload in the pinned TC32 startup model."""

import argparse
import hashlib
from pathlib import Path
import re

from bseed_ota_identity import parse_ota_header
from bseed_pm_release import extract_native_image


BOOT_SLOTS = (0, 0x40000)
LOOP_ENTRIES = 3
CYCLE_LIMIT = 4_000_000


class BootFailure(RuntimeError):
    pass


def trace_boot(image, symbols, slot):
    import tc32emu as emu

    flash = emu.Flash()
    flash.mem[slot:slot + len(image)] = image
    machine = emu.Machine(flash, boot_slot=slot, symbols=symbols, cpi=3)
    seen = set()
    loop_entries = 0

    def skip_stack_paint(m):
        # The SDK diagnostic fill starts at an address this model rejects.
        m.r[15] = symbols['COPY_DATA']

    def crystal_ready(m):
        # Model a running timer and assume the TLSR8258 crystal probe succeeds.
        m.reg_write(0x74a, 1, 0xc3)
        m.r[15] = m.r[14] & ~1

    def app_task(_):
        nonlocal loop_entries
        loop_entries += 1
        if loop_entries == LOOP_ENTRIES:
            raise emu.Stop('application loop reached the required number of entries')

    machine.hooks[symbols['FLL_STK']] = skip_stack_paint
    machine.hooks[symbols['pm_wait_xtal_ready']] = crystal_ready
    required = {'hal_gpio_callback', 'drv_wd_start'}
    for name in sorted(required):
        machine.hooks[symbols[name]] = lambda _, name=name: seen.add(name)
    machine.hooks[symbols['app_task']] = app_task

    try:
        reason = machine.run(CYCLE_LIMIT)
    except (emu.EmuError, emu.SoftwareReset, emu.WatchdogReset, emu.Stop) as error:
        reason = f'{type(error).__name__}: {error}'
    location = machine.symbolize(machine.r[15])
    passed = loop_entries == LOOP_ENTRIES and seen == required
    print(f'Slot {slot:#07x}: {sorted(seen)}, application loop entries={loop_entries}; '
          f'{location}: {reason}', flush=True)
    return passed, location, reason


def read_payload(ota_path, expected_hash):
    ota = ota_path.read_bytes()
    if hashlib.sha256(ota).hexdigest() != expected_hash:
        raise BootFailure(f'{ota_path}: OTA hash differs from the verified artifact')
    image = extract_native_image(ota, parse_ota_header(ota))
    for offset in (0x8008, 0x20008):
        if image[offset:offset + 4] == b'KNLT':
            raise BootFailure(f'{ota_path}: unexpected legacy boot marker at {offset:#x}')
    return image


def read_symbols(layout_path):
    return {
        name: int(address, 16)
        for address, name in re.findall(
            r'^([0-9a-f]{8})[ \t]+[lgw][ \t]+(?:[FO][ \t]+)?'
            r'\.[\w.]+[ \t]+[0-9a-f]+[ \t]+(\S+)$',
            layout_path.read_text(encoding='utf8'), re.M)
    }


def verify_boot(ota_path, layout_path, expected_hash, *, expect_r21_fault=False):
    image = read_payload(ota_path, expected_hash)
    symbols = read_symbols(layout_path)
    print(f'Booting {ota_path} (OTA SHA-256 {expected_hash})', flush=True)
    failures = []
    for slot in BOOT_SLOTS:
        passed, location, reason = trace_boot(image, symbols, slot)
        if expect_r21_fault:
            # The negative control must hit the known fault, even if some
            # other kind of rejection would also block a release candidate.
            passed = (not passed and location.startswith('flash_vdd_f_calib+')
                      and 'EmuError: unaligned 32-bit access 0x0000b402' in str(reason))
        if not passed:
            failures.append(f'{slot:#07x} ({location}: {reason})')
    if failures:
        expected = 'Expected r21 fault missing' if expect_r21_fault else 'Cold boot failed'
        raise BootFailure(f'{ota_path}: {expected} in ' + ', '.join(failures))
    if expect_r21_fault:
        print('Reproduced the expected r21 flash-calibration failure in both slots.', flush=True)
        return None
    return boot_evidence(expected_hash)


def boot_evidence(ota_hash):
    return {'otaSha256': ota_hash, 'slots': list(BOOT_SLOTS),
            'appLoopEntriesPerSlot': LOOP_ENTRIES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ota', type=Path)
    parser.add_argument('layout', type=Path)
    parser.add_argument('ota_sha256')
    parser.add_argument('--expect-r21-fault', action='store_true')
    args = parser.parse_args()
    verify_boot(args.ota, args.layout, args.ota_sha256, expect_r21_fault=args.expect_r21_fault)


if __name__ == '__main__':
    main()
