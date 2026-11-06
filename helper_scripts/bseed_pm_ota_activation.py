"""Check OTA activation with exact source firmware and modeled power loss."""

import argparse
from pathlib import Path

import tc32emu as emu

from bseed_pm_boot import BOOT_SLOTS, BootFailure, read_payload, read_symbols


MARKER_OFFSET = 8
ENABLED = 0x4B
DISABLED = 0
ERASED = 0xFF
ACTIVATION_CYCLE_LIMIT = 30_000_000
RETURN_PC = 0x1FE


def activate(source, staged, symbols, old_slot, new_slot, *, valid):
    flash = emu.Flash()
    flash.mem[old_slot:old_slot + len(source)] = source
    flash.mem[new_slot:new_slot + len(staged)] = staged
    old_marker = old_slot + MARKER_OFFSET
    new_marker = new_slot + MARKER_OFFSET
    flash.mem[new_marker] = ERASED  # The downloader holds the marker until activation.

    def check_power_on(label, markers, expected_slot):
        # Give a separate machine the exact flash contents at this point.
        # Its power-on starts with fresh CPU state and selects the slot itself.
        snapshot = emu.Flash()
        snapshot.mem[:] = flash.mem
        observed = (snapshot.mem[old_marker], snapshot.mem[new_marker])
        if observed != markers:
            raise BootFailure(f'{label}: markers {observed!r}, expected {markers!r}')
        selected = emu.Machine(snapshot).boot_slot
        if selected != expected_slot:
            raise BootFailure(f'{label}: boot selected {selected:#07x}, expected {expected_slot:#07x}')
        print(f'{old_slot:#07x} -> {new_slot:#07x}, {label}: selected {selected:#07x}', flush=True)

    marker_writes = []

    def after_write(operation, address, length):
        if operation != 'program':
            return
        marker_writes.append(address)
        if not valid or length != 1:
            raise BootFailure('Unexpected flash program during OTA activation')
        if marker_writes == [new_marker]:
            check_power_on('power loss after enabling staged slot',
                           (ENABLED, ENABLED), min(old_slot, new_slot))
        elif marker_writes == [new_marker, old_marker]:
            check_power_on('power loss after retiring previous slot',
                           (DISABLED, ENABLED), new_slot)
        else:
            raise BootFailure(f'Unexpected slot marker write sequence: {marker_writes!r}')

    check_power_on('power loss before activation', (ENABLED, ERASED), old_slot)
    machine = emu.Machine(flash, boot_slot=old_slot, symbols=symbols)
    flash.on_write = after_write
    machine.reg_write(0x74A, 1, 0xC3)  # Normal startup enables the timer.
    machine.write(symbols['mcuBootAddr'], 4, old_slot)
    machine.r[13] = 0x850000
    machine.r[14] = RETURN_PC
    machine.r[15] = symbols['ota_mcuReboot']

    def returned(_):
        raise emu.Stop('activation returned without reset')

    machine.hooks[RETURN_PC] = returned
    reset = rejected = False
    try:
        machine.run(ACTIVATION_CYCLE_LIMIT)
    except emu.SoftwareReset:
        reset = True
    except emu.Stop as error:
        rejected = str(error) == 'activation returned without reset'
    if valid:
        if not reset or marker_writes != [new_marker, old_marker]:
            raise BootFailure('Valid staged image did not complete activation and reset')
        check_power_on('completed activation', (DISABLED, ENABLED), new_slot)
    else:
        if not rejected or marker_writes:
            raise BootFailure('Invalid staged image did not return without modifying markers')
        check_power_on('rejected staging', (ENABLED, ERASED), old_slot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_ota', type=Path)
    parser.add_argument('source_symbols', type=Path)
    parser.add_argument('source_hash')
    parser.add_argument('staged_ota', type=Path)
    parser.add_argument('staged_hash')
    args = parser.parse_args()
    source = read_payload(args.source_ota, args.source_hash)
    staged = read_payload(args.staged_ota, args.staged_hash)
    symbols = read_symbols(args.source_symbols)
    corrupt = staged[:-1] + bytes([staged[-1] ^ 1])
    for label, payload, valid in (
        ('complete image', staged, True),
        ('partial download', staged[:len(staged) // 2], False),
        ('corrupt image CRC', corrupt, False),
    ):
        print(f'Testing OTA activation with {label}', flush=True)
        for old_slot, new_slot in (BOOT_SLOTS, BOOT_SLOTS[::-1]):
            activate(source, payload, symbols, old_slot, new_slot, valid=valid)


if __name__ == '__main__':
    main()
