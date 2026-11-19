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

import telink_swire_recovery as swire

IMAGE = swire.STATE_ROOT / 'tools' / 'zg204zl' / 'ZG204ZL_v0140.bin'
IMAGE_URL = ('https://raw.githubusercontent.com/pvvx/ZigbeeTLc/'
             'c249d8a7caddb5012d06aba580d4a53d1957f9c1/bin/ZG204ZL_v0140.bin')
IMAGE_SHA = '792bb4452a249e1408e316d37a0daadf11626343f7f625ebaa9815497d04807a'
IMAGE_BYTES = 129140
FLASH_BYTES = 0x80000
ERASE_END = 0x20000
RUNS = swire.STATE_ROOT / 'zg204zl-runs'


def digest(data):
    return hashlib.sha256(data).hexdigest()


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


def run(port, seconds):
    from serial.tools import list_ports
    deadline = time.monotonic() + seconds
    print(f'ZG-204ZL | ZigbeeTLc v0.1.4.0 | {port}', flush=True)
    print('Use the same qualified sensor wiring and supply as before. Keep contacts fixed throughout.', flush=True)
    print(f'Waiting up to {seconds} seconds for the USB adapter...', flush=True)
    while port.upper() not in {p.device.upper() for p in list_ports.comports()}:
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{port} did not appear. Check USB/port, then run again.')
        time.sleep(1)
    if IMAGE.exists():
        image = IMAGE.read_bytes()
    else:
        print('Downloading the pinned ZG-204ZL firmware...', flush=True)
        image = swire.download(IMAGE_URL, IMAGE_BYTES)
    if len(image) != IMAGE_BYTES or digest(image) != IMAGE_SHA:
        raise RuntimeError('ZG-204ZL firmware size/hash mismatch. Nothing written.')
    if not IMAGE.exists():
        IMAGE.parent.mkdir(parents=True, exist_ok=True)
        temporary = IMAGE.with_name(IMAGE.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_bytes(image)
        temporary.replace(IMAGE)
    folder = swire.private_output(RUNS / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]))
    folder.mkdir(parents=True)
    def report(message):
        print(message, file=sys.stderr, flush=True)
        with (folder / 'progress.log').open('a', encoding='utf-8') as stream:
            stream.write(datetime.datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
    state_path = swire.STATE_ROOT / 'zg204zl' / (swire.port_key(port) + '.json')
    state = {'device': 'ZG-204ZL', 'port': port, 'folder': str(folder), 'imageSha256': IMAGE_SHA,
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
            snapshot = folder / 'ZG204ZL_v0140.bin'
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
            if digest(snapshot.read_bytes()) != IMAGE_SHA:
                raise RuntimeError('Prepared image changed; write cancelled.')
            report('[2/4] Writing firmware at 0x00000; erases only 0x00000..0x1FFFF.')
            state['writeAttempted'] = True
            save('WRITING')
            writer(reader, snapshot, port, folder, report)
            report('[3/4] Reading back ONCE (512 KiB), keeping the CPU stopped.')
            save('READBACK')
            swire._read_flash(port, folder / 'after.bin', reader=reader, halt=False, reads=1, out=report)
            report('[4/4] Comparing firmware, erased tail and ALL preserved bytes.')
            after = (folder / 'after.bin').read_bytes()
            verify(before, after, image)
            state['afterSha256'] = digest(after)
            state['preservationVerified'] = True
            save('VERIFIED')
            report('SUCCESS: firmware and preserved data verified. CPU is still stopped.')
            report('Power-cycle the sensor to boot; then check pairing and motion in Zigbee2MQTT.')
        except BaseException:
            save('FAILED')
            report('STOPPED. ' + ('Write may have changed flash. Do not retry or power-cycle until inspected.'
                                  if state['writeAttempted'] else 'No firmware write was attempted.'))
            raise
        finally:
            report('Backup, receipt and logs: ' + str(folder))


def main():
    parser = argparse.ArgumentParser(description='ZG-204ZL only: one backup, flash, one verifying read. No automatic restart.')
    parser.add_argument('port', nargs='?', default='COM3', help='adapter COM port (default COM3)')
    parser.add_argument('--seconds', type=int, default=300, help='USB/target connection window, 1..900 seconds')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 900 or not re.fullmatch(r'COM\d+', args.port, re.I):
        parser.error('use a COM port and --seconds 1..900')
    try:
        run(args.port.upper(), args.seconds)
        return 0
    except KeyboardInterrupt:
        print('\nCancelled. Read the saved summary before another write.', file=sys.stderr)
        return 130
    except Exception as error:
        print('\nERROR: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
