"""Keep PM NVM migration bounded without destroying historical energy.

Repeated migration failures must not reboot-loop, but fallback never
authorizes erasing original energy/calibration/protection records.
Real stub boots exercise file-backed NVM across simulated resets.
"""
import shutil
import struct
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NVM_DIR = ROOT / "stub_nvm_data"
PM_BIN = ROOT / "build" / "stub" / "stub_pm_quarantine"
PM_CONFIG = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"

ATTEMPTS_ITEM = 52
LEGACY_ITEMS = (40, 44, 51)


def _item(item_id: int) -> Path:
    return NVM_DIR / f"item_{item_id:02x}.bin"


def _clean_nvm() -> None:
    shutil.rmtree(NVM_DIR, ignore_errors=True)
    NVM_DIR.mkdir(parents=True, exist_ok=True)


def _write_device_config(config: str) -> None:
    raw = config.encode("ascii")
    assert len(raw) < 128
    _item(2).write_bytes(struct.pack("<H", len(raw)) + raw.ljust(128, b"\0"))


def _seed_legacy(energy: bytes = struct.pack("<Q", 4242),
                 calibration: bytes = struct.pack("<IIII", 0x484C5743,
                                                  161460, 144679, 16989),
                 overload: bytes = struct.pack("<8H", 2100, 10000, 5,
                                               26000, 18000, 30, 3680, 16000)):
    _item(40).write_bytes(energy)
    _item(44).write_bytes(calibration)
    _item(51).write_bytes(overload)


def _run(env=None):
    merged = dict(__import__("os").environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [str(PM_BIN)],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        timeout=15,
        env=merged,
    )


def _attempts() -> int | None:
    path = _item(ATTEMPTS_ITEM)
    if not path.exists():
        return None
    (value,) = struct.unpack("<I", path.read_bytes()[:4])
    return value


@pytest.fixture(scope="module")
def pm_stub():
    if not shutil.which("make") or not shutil.which("gcc"):
        pytest.skip("host C toolchain required to build the PM stub")
    subprocess.run(
        [
            "make", "-C", "src/stub", "build",
            f"BINARY={PM_BIN}",
            "BSEED_PM_B28WRPVX=1",
            "BSEED_PM_B28WRPVX_PROTECTION=1",
            "HLW8012_VOLTAGE_MULTIPLIER=161460",
            "HLW8012_CURRENT_MULTIPLIER=144679",
            "HLW8012_POWER_MULTIPLIER=16989",
        ],
        cwd=ROOT,
        check=True,
    )
    assert PM_BIN.exists()
    yield PM_BIN
    _clean_nvm()


def test_repeated_write_failure_preserves_legacy_after_bounded_attempts(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _seed_legacy()
    env = {"STUB_NVM_FAIL_WRITE": "64@1"}

    for expected in (1, 2, 3):
        result = _run(env)
        assert result.returncode == 0
        assert "legacy nvm preserved" not in result.stdout.lower()
        assert _attempts() == expected
        for item in LEGACY_ITEMS:
            assert _item(item).exists()

    final = _run(env)
    assert final.returncode == 0
    assert "legacy nvm preserved" in final.stdout.lower()
    for item in LEGACY_ITEMS:
        assert _item(item).exists()
    assert _attempts() == 3


def test_capped_counter_preserves_legacy_on_next_boot(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _seed_legacy()
    _item(ATTEMPTS_ITEM).write_bytes(struct.pack("<I", 3))

    result = _run()
    assert result.returncode == 0
    assert "legacy nvm preserved" in result.stdout.lower()
    for item in LEGACY_ITEMS:
        assert _item(item).exists()


def test_corrupt_size_record_fails_gracefully_and_counts(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _item(40).write_bytes(b"\x01\x02\x03")  # 3 bytes, not 8

    result = _run()
    assert result.returncode == 0
    assert _attempts() == 1


def test_uncountable_counter_write_still_boots_without_erasing(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _seed_legacy()

    result = _run({"STUB_NVM_FAIL_WRITE": "64@1,52@1"})
    assert result.returncode == 0
    assert "legacy nvm preserved" in result.stdout.lower()
    for item in LEGACY_ITEMS:
        assert _item(item).exists()



def test_unified_zero_and_higher_legacy_energy_are_never_overwritten(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _seed_legacy(energy=struct.pack("<Q", 750))
    # This could be an explicit user reset, so max(old,new) would be unsafe.
    _item(64).write_bytes(struct.pack("<Q", 0))
    result = _run()
    assert result.returncode == 0
    assert "energy conflict old=750 Wh new=0 Wh" in result.stdout
    assert struct.unpack("<Q", _item(40).read_bytes())[0] == 750
    assert struct.unpack("<Q", _item(64).read_bytes())[0] == 0


def test_invalid_legacy_uint48_energy_kept_for_supervised_recovery(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    _seed_legacy(energy=struct.pack("<Q", 1 << 48))
    result = _run()
    assert result.returncode == 0
    assert "invalid legacy energy" in result.stdout.lower()
    assert _attempts() == 1
    assert _item(40).exists()
    assert not _item(64).exists()
