"""Crash-safe, process-scoped transaction guard for one Zigbee network authority.

All BSEED network-lock writers take this OS lock. The guard file is persistent:
unlike an O_EXCL 'stale PID' lock, kernel locks disappear on process death.
The guard location must be on the same shared filesystem for every runner.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def network_mutex(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = path.with_name(path.name + '.mutex')
    # Never delete this file: the kernel lock, not file existence, is authority.
    with guard.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'1')
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pending_path(path):
    path = Path(path)
    return path.with_name(path.name + '.terminalizing')


def require_no_pending(path):
    """Block new leases while a crash-recovery transaction is unfinished."""
    if pending_path(path).exists():
        raise RuntimeError('Network terminalization pending; recover audited transaction first')
