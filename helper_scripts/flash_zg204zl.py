"""Local ZG-204ZL bench launcher. Uses the existing pinned SWire primitives.

One full backup, a native pvvx write at zero, and one full verified readback.
No automatic write retries, CPU restart, pairing or mains operation.
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

import telink_swire_recovery as swire

IMAGE = swire.STATE_ROOT / 'tools' / 'zg204zl' / 'ZG204ZL_v0140.bin'
IMAGE_URL = ('https://raw.githubusercontent.com/pvvx/ZigbeeTLc/'
             'c249d8a7caddb5012d06aba580d4a53d1957f9c1/bin/ZG204ZL_v0140.bin')
IMAGE_SHA = '792bb4452a249e1408e316d37a0daadf11626343f7f625ebaa9815497d04807a'
IMAGE_BYTES = 129140
FLASH_BYTES = 0x80000
ERASE_END = 0x20000
RUNS = swire.STATE_ROOT / 'zg204zl-runs'
V0145_FILENAME = 'ZG204ZL_v0145.bin'
V0145_SHA = 'd31624949aeec6f95fd983899e1b679dbd7a44db209a690d6a2ded57e3cc1031'
V0145_BYTES = 130628
V0145_ARTIFACT = 'https://github.com/analienx/tuya-zigbee-switch/actions/runs/44706913369/artifacts/14615831032'
VANT_BUILD = 'https://github.com/analienx/tuya-zigbee-switch/actions/runs/44788806210'


class Firmware(NamedTuple):
    filename: str
    sha256: str
    size: int
    cache: Path
    source: Optional[str] = None
    artifact: Optional[str] = None


def firmware_profiles():
    return {
        'v0140': Firmware('ZG204ZL_v0140.bin', IMAGE_SHA, IMAGE_BYTES, IMAGE, IMAGE_URL),
        'v0145': Firmware(V0145_FILENAME, V0145_SHA, V0145_BYTES,
                          IMAGE.with_name(V0145_FILENAME), artifact=V0145_ARTIFACT),
        'v0146': Firmware('ZG204ZL_v0146.bin',
                          '08ccff0e662f9f65fb9e2b486023a974ca0c075367d8edabe935a660567ba90e',
                          130628, IMAGE.with_name('ZG204ZL_v0146.bin'),
                          artifact=f'{VANT_BUILD}/artifacts/14654568352'),
        'v0147': Firmware('ZG204ZL_v0147.bin',
                          'cd9903e291c7052256c5471478300b763db2abcb49ec5194b974b10c1358bcce',
                          130628, IMAGE.with_name('ZG204ZL_v0147.bin'),
                          artifact=f'{VANT_BUILD}/artifacts/14655910771'),
        'v0148': Firmware('ZG204ZL_v0148.bin',
                          '161ae8a21a920a4156ddc82a7434266b2eae517a31e760e1078536a390c79537',
                          130660, IMAGE.with_name('ZG204ZL_v0148.bin'),
                          artifact='https://github.com/analienx/tuya-zigbee-switch/actions/runs/44793479388/artifacts/14657327700'),
    }


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_firmware(firmware, image_path):
    if image_path is not None:
        data = image_path.read_bytes()
    elif firmware.cache.exists():
        data = firmware.cache.read_bytes()
    elif firmware.source:
        print('Downloading the pinned ZG-204ZL firmware...', flush=True)
        data = swire.download(firmware.source, firmware.size)
    else:
        raise RuntimeError(f'Download {firmware.filename} from {firmware.artifact} '
                           'and pass its path with --image.')
    if len(data) != firmware.size or len(data) > ERASE_END or digest(data) != firmware.sha256:
        raise RuntimeError('ZG-204ZL firmware size/hash mismatch. Nothing written.')
    if image_path is None and not firmware.cache.exists():
        firmware.cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = firmware.cache.with_name(firmware.filename + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(firmware.cache)
    return data


def require_candidate_layout(before):
    # The documented stock and ZigbeeTLc images both begin at zero on this board.
    identity = (int.from_bytes(before[18:20], 'little'), int.from_bytes(before[20:22], 'little'))
    size = int.from_bytes(before[24:28], 'little')
    if (before[6:12] != b'\x5d\x02KNLT' or identity not in {(0x1286, 0x0203), (0x1141, 0x0232)}
            or not 4096 <= size <= ERASE_END):
        raise RuntimeError('Backup does not show a supported ZG-204ZL application at zero. Inspect before writing.')
    # The ZigbeeTLc boot path can copy an active OTA image from 0x20000.
    # Refuse either alternate boot area rather than trying to disable it.
    for offset in (0x20000, 0x40000):
        if before[offset + 8:offset + 12] == b'KNLT':
            raise RuntimeError(f'Backup has an active alternate image at 0x{offset:05X}. Inspect before writing.')


def verify(before, after, image):
    if len(before) != FLASH_BYTES or len(after) != FLASH_BYTES:
        raise RuntimeError('Incomplete 512 KiB flash read.')
    expected = image + b'\xff' * (ERASE_END - len(image)) + before[ERASE_END:]
    if after != expected:
        first = next(i for i, (a, b) in enumerate(zip(after, expected)) if a != b)
        region = 'firmware' if first < len(image) else ('erased tail' if first < ERASE_END else 'preserved data')
        raise RuntimeError(f'{region} mismatch at 0x{first:05X}; keep the sensor connected for inspection.')


def writer(reader, image, port, folder, report):
    # No activation/reset/run flags: the backup already left the CPU stopped.
    if digest(reader.read_bytes()) != swire.READER_SHA256:
        raise RuntimeError('Pinned programmer changed; write cancelled.')
    with (folder / 'write.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-u', str(reader), '-p', port, '-b', '921600',
                                  'wf', '0x0', str(image)], stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        started = last_change = last_report = time.monotonic()
        previous = None
        try:
            while child.poll() is None:
                now = time.monotonic()
                tail = swire.log_tail(folder / 'write.log')
                addresses = re.findall(r'(Write to|Erase Sector at) 0x([0-9a-fA-F]+)', tail)
                current = addresses[-1] if addresses else None
                if current != previous:
                    previous, last_change = current, now
                if now - started > 1800 or now - last_change > 120:
                    raise RuntimeError('Programming stopped making progress; write is uncertain. Do not retry.')
                if now - last_report >= 5:
                    detail = f'{current[0]} 0x{int(current[1], 16):05X}' if current else 'connecting'
                    report(f'  Writing: {detail} ({int(now - started)} s); verification follows.')
                    last_report = now
                time.sleep(0.2)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        if child.returncode:
            raise RuntimeError(f'Programmer exited {child.returncode}. See write.log; do not retry the write.')


def confirm_stopped(port, divider):
    with swire.open_port(port, timeout=0.02, baud=921600) as connection:
        probe = swire.SwireDiagnostic(connection, 921600)
        probe.write_register(0xB2, bytes([divider]))
        if [probe.read_register(0xB2)[0] for _ in range(3)] != [divider] * 3:
            raise RuntimeError('Saved SWire divider is not stable; no flash read started.')
        if [probe.read_register(0x602)[0] for _ in range(2)] != [5, 5]:
            raise RuntimeError('CPU is no longer stopped; no reset or flash read attempted.')


def readback(port, output, reader, backup_receipt, report):
    """Use the backup's qualified divider instead of a marginal auto-scan."""
    checks = backup_receipt.get('haltChecks', [])
    if not checks or checks[-1].get('cpuControls') != [5, 5]:
        raise RuntimeError('Backup has no confirmed stopped-CPU/divider evidence.')
    divider = checks[-1]['divider']
    clock_mhz = round(divider * 921600 / 2_000_000)
    if round(clock_mhz * 2_000_000 / 921600) != divider:
        raise RuntimeError('Saved divider cannot be represented by the pinned reader clock option; inspect before reading.')
    confirm_stopped(port, divider)
    report(f'  Reusing backup divider {divider} (-c {clock_mhz}); no reset or activation.')
    def runner(command, **_kwargs):
        # Only rf is permitted; no -t, -r or write/erase command is added.
        command = [*command[:2], '-c', str(clock_mhz), *command[2:]]
        result = swire.run_reader_with_progress(command, log_path=output.with_name(output.name + '.clock.log'),
                                                size=FLASH_BYTES, label='Verify read', out=report)
        # Upstream may silently fall back to another clock after -c fails.
        # Keep that as failed evidence rather than claiming this timing worked.
        log = output.with_name(output.name + '.clock.log').read_text(encoding='utf-8', errors='replace')
        expected = f'SWire speed for CLK {clock_mhz:.1f} MHz... ok.'
        if result.returncode == 0 and expected not in log:
            raise RuntimeError('Pinned reader did not confirm the saved timing; inspect its clock log.')
        return result
    return swire._read_flash(port, output, reader=reader, halt=False, reads=1, runner=runner, out=report)


def verify_only(port):
    """Resume comparison of the existing write; never call the writer or HALT."""
    state_path = swire.STATE_ROOT / 'zg204zl' / (swire.port_key(port) + '.json')
    with swire.PortGuard(port):
        state = swire.read_json(state_path)
        if state.get('port') != port or state.get('device') != 'ZG-204ZL' or not state.get('writeAttempted'):
            raise RuntimeError('No matching sensor write is recorded for this port.')
        folder = swire.private_output(state['folder'])
        authority = swire.authority_path(port)
        if authority.exists() and swire.read_json(authority)['phase'] not in swire.HALT_READ_PHASES:
            raise RuntimeError('An existing recovery owns this port.')
        firmware = next((f for f in firmware_profiles().values() if f.sha256 == state.get('imageSha256')), None)
        if firmware is None:
            raise RuntimeError('Recorded image is not a supported firmware pin. Verification stopped.')
        image = (folder / firmware.filename).read_bytes()
        before = (folder / 'before.bin').read_bytes()
        if (len(image) != firmware.size or digest(image) != firmware.sha256
                or len(before) != FLASH_BYTES or digest(before) != state.get('backupSha256')):
            raise RuntimeError('Original image/backup evidence changed; verification stopped.')
        receipt = swire.read_json(folder / 'before.bin.json')
        if (receipt.get('port') != port or receipt.get('status') != 'READ_SINGLE'
                or [r.get('sha256') for r in receipt.get('reads', [])] != [digest(before)]):
            raise RuntimeError('Original backup receipt does not match this sensor operation.')
        def report(message):
            print(message, file=sys.stderr, flush=True)
            with (folder / 'progress.log').open('a', encoding='utf-8') as stream:
                stream.write(datetime.datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
        output = folder / ('verify-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.bin')
        reader = Path(swire.ensure_reader(out=report)['reader'])
        report('VERIFICATION ONLY: read existing flash; no erase, programming or CPU reset.')
        report('Keep the same sensor powered and attached.')
        attempt = {'output': str(output), 'status': 'STARTED', 'cpuResetRequested': False, 'flashWriteAttempted': False}
        state.setdefault('verificationAttempts', []).append(attempt)
        def save():
            swire.atomic_json(folder / 'summary.json', state)
            swire.atomic_json(state_path, state)
        save()
        try:
            readback(port, output, reader, receipt, report)
            after = output.read_bytes()
            verify(before, after, image)
            attempt['status'] = 'VERIFIED'
            state.update(phase='VERIFIED', afterSha256=digest(after), preservationVerified=True,
                         verifiedReadback=str(output), bootVerified=False)
            save()
            report('SUCCESS: firmware, erased tail and all preserved data match.')
            report('The comparison passed. Power-cycle the sensor to boot and check Zigbee pairing/motion.')
        except BaseException as error:
            attempt.update(status='FAILED', error=str(error))
            state['phase'] = 'FAILED'
            save()
            raise
        finally:
            report('Evidence: ' + str(folder))


def run(port, seconds, version='v0140', image_path=None):
    from serial.tools import list_ports
    firmware = firmware_profiles()[version]
    print(f'ZG-204ZL | {firmware.filename} | {port}', flush=True)
    image = load_firmware(firmware, image_path)
    deadline = time.monotonic() + seconds
    print('Use the same qualified sensor wiring and supply as before. Keep contacts fixed throughout.', flush=True)
    print(f'Waiting up to {seconds} seconds for the USB adapter...', flush=True)
    while port.upper() not in {p.device.upper() for p in list_ports.comports()}:
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{port} did not appear. Check USB/port, then run again.')
        time.sleep(1)
    folder = swire.private_output(RUNS / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]))
    folder.mkdir(parents=True)
    def report(message):
        print(message, file=sys.stderr, flush=True)
        with (folder / 'progress.log').open('a', encoding='utf-8') as stream:
            stream.write(datetime.datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
    state_path = swire.STATE_ROOT / 'zg204zl' / (swire.port_key(port) + '.json')
    state = {'device': 'ZG-204ZL', 'port': port, 'folder': str(folder), 'imageSha256': firmware.sha256,
             'imageVersion': version,
             'phase': 'PREPARING', 'writeAttempted': False, 'bootVerified': False,
             'backupReadCount': 1, 'verificationReadCount': 1}
    def save(phase):
        state['phase'] = phase
        swire.atomic_json(folder / 'summary.json', state)
        swire.atomic_json(state_path, state)
    with swire.PortGuard(port):
        authority = swire.authority_path(port)
        if authority.exists() and swire.read_json(authority)['phase'] not in swire.HALT_READ_PHASES:
            raise RuntimeError('An existing recovery owns this port. Inspect its status before continuing.')
        if state_path.exists():
            previous = swire.read_json(state_path)
            if previous.get('writeAttempted') and previous.get('phase') != 'VERIFIED':
                raise RuntimeError('Previous sensor write requires inspection: ' + previous['folder'])
        save('PREPARING')
        try:
            reader = Path(swire.ensure_reader(out=report)['reader'])
            snapshot = folder / firmware.filename
            snapshot.write_bytes(image)
            report('[1/4] Reading your sensor backup ONCE (512 KiB).')
            save('BACKUP')
            swire._read_flash(port, folder / 'before.bin', reader=reader, halt=True,
                              reads=1, retry_seconds=max(0, int(deadline - time.monotonic())), out=report)
            before = (folder / 'before.bin').read_bytes()
            if len(before) != FLASH_BYTES:
                raise RuntimeError('Backup is incomplete; write cancelled.')
            state['backupSha256'] = digest(before)
            if before == b'\xff' * FLASH_BYTES or before == b'\x00' * FLASH_BYTES:
                raise RuntimeError('Blank-looking backup; inspect wiring before any write.')
            if version != 'v0140':
                require_candidate_layout(before)
            if digest(snapshot.read_bytes()) != firmware.sha256:
                raise RuntimeError('Prepared image changed; write cancelled.')
            report('[2/4] Writing firmware at 0x00000; erases only 0x00000..0x1FFFF.')
            state['writeAttempted'] = True
            save('WRITING')
            writer(reader, snapshot, port, folder, report)
            report('[3/4] Reading back ONCE (512 KiB), keeping the CPU stopped.')
            save('READBACK')
            readback(port, folder / 'after.bin', reader,
                     swire.read_json(folder / 'before.bin.json'), report)
            report('[4/4] Comparing firmware, erased tail and ALL preserved bytes.')
            save('COMPARISON')
            after = (folder / 'after.bin').read_bytes()
            verify(before, after, image)
            state['afterSha256'] = digest(after)
            state['preservationVerified'] = True
            save('VERIFIED')
            report('SUCCESS: firmware and preserved data verified. CPU is still stopped.')
            report('Power-cycle the sensor to boot; then check pairing and motion in Zigbee2MQTT.')
        except BaseException as error:
            state['failedPhase'] = state['phase']
            state['lastError'] = str(error)
            save('FAILED')
            report('STOPPED. ' + ('Write may have changed flash. Do not retry or power-cycle until inspected.'
                                  if state['writeAttempted'] else 'No firmware write was attempted.'))
            if state['failedPhase'] == 'READBACK' and isinstance(error, Exception):
                raise RuntimeError('Write was sent, but verification read failed. Resume with --verify-only; '
                                   'do not run the flash operation again. Details are saved in the read receipt.') from error
            raise
        finally:
            report('Backup, receipt and logs: ' + str(folder))


def main():
    parser = argparse.ArgumentParser(description='ZG-204ZL only: one backup, flash, one verifying read. No automatic restart.')
    parser.add_argument('port', nargs='?', default='COM3', help='adapter COM port (default COM3)')
    parser.add_argument('--seconds', type=int, default=300, help='USB/target connection window, 1..900 seconds')
    parser.add_argument('--version', choices=firmware_profiles(), help='choose a pinned image (default v0140)')
    parser.add_argument('--image', type=Path, help='use a local native file matching the selected firmware pin')
    parser.add_argument('--verify-only', action='store_true', help='read and verify the last write on this port, WITHOUT rewriting/resetting')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 900 or not re.fullmatch(r'COM\d+', args.port, re.I):
        parser.error('use a COM port and --seconds 1..900')
    if args.verify_only and (args.version is not None or args.image is not None):
        parser.error('--verify-only uses the saved image. Omit --version and --image.')
    try:
        if args.verify_only:
            verify_only(args.port.upper())
        else:
            run(args.port.upper(), args.seconds, args.version or 'v0140', args.image)
        return 0
    except KeyboardInterrupt:
        print('\nCancelled. Read the saved summary before another write.', file=sys.stderr)
        return 130
    except Exception as error:
        print('\nERROR: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
