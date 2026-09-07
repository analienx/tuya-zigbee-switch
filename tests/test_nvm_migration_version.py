"""Tests for migration schema version convergence (Romasku #492 adaptation)."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path


BINARY_V1 = Path("build/stub/stub_device")
BINARY_V2 = Path("build/stub/stub_device_schema_v2")
NVM_DIR = Path("stub_nvm_data")
VERSION_ITEM = NVM_DIR / "item_01.bin"


def _write_version(value: int) -> None:
    NVM_DIR.mkdir(exist_ok=True)
    VERSION_ITEM.write_bytes(struct.pack("<H", value))


def _read_version() -> int:
    return struct.unpack("<H", VERSION_ITEM.read_bytes())[0]


def _build_v2_stub() -> None:
    subprocess.run(
        [
            "make",
            "-C",
            "src/stub",
            "build",
            "BINARY=../../build/stub/stub_device_schema_v2",
            "NVM_MIGRATIONS_VERSION=2",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )


def _run(binary: Path = BINARY_V1) -> str:
    proc = subprocess.run(
        [str(binary), "--device-config", "Stub;Stub;", "--freeze-time"],
        input="q\n",
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    return output


def test_unknown_schema_establishes_current_version() -> None:
    assert not VERSION_ITEM.exists()
    _run()
    assert _read_version() == 1


def test_successful_forward_migration_advances_once_and_converges() -> None:
    _build_v2_stub()
    _write_version(1)

    first = _run(BINARY_V2)
    assert "Old version: 1" in first
    assert "Current version: 2" in first
    assert _read_version() == 2

    before = VERSION_ITEM.stat().st_mtime_ns
    second = _run(BINARY_V2)
    after = VERSION_ITEM.stat().st_mtime_ns

    assert "Old version: 2" in second
    assert "Current version: 2" in second
    assert _read_version() == 2
    assert after == before


def test_newer_schema_is_never_implicitly_downgraded() -> None:
    _write_version(2)
    output = _run(BINARY_V1)

    assert "Old version: 2" in output
    assert "Current version: 1" in output
    assert "refusing schema downgrade" in output
    assert _read_version() == 2


def test_current_schema_does_not_rewrite_version_item() -> None:
    _write_version(1)
    before = VERSION_ITEM.stat().st_mtime_ns
    output = _run()
    after = VERSION_ITEM.stat().st_mtime_ns

    assert "Old version: 1" in output
    assert "Current version: 1" in output
    assert _read_version() == 1
    assert after == before


def test_source_contract_never_advances_version_after_failed_migration() -> None:
    source = Path("src/device_config/nvm_migrations.c").read_text(encoding="utf-8")
    failure_gate = source.index("if (!run_pending_migrations(oldVersion))")
    failure_return = source.index("return;", failure_gate)
    final_write = source.rindex("write_version_to_nv(currentVersion);")

    assert failure_gate < failure_return < final_write
    assert "static bool run_pending_migrations" in source
