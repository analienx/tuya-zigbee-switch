import shutil
import struct
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NVM_DIR = ROOT / "stub_nvm_data"
PM_BIN = ROOT / "build" / "stub" / "stub_pm_v8"
GENERIC_BIN = ROOT / "build" / "stub" / "stub_device"
PM_CONFIG = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"
OTHER_CONFIG = "other;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"


def _item(item_id: int) -> Path:
    return NVM_DIR / f"item_{item_id:02x}.bin"


def _clean_nvm() -> None:
    shutil.rmtree(NVM_DIR, ignore_errors=True)
    NVM_DIR.mkdir(parents=True, exist_ok=True)


def _write_device_config(config: str) -> None:
    raw = config.encode("ascii")
    assert len(raw) < 128
    _item(2).write_bytes(struct.pack("<H", len(raw)) + raw.ljust(128, b"\0"))


def _run(binary: Path, commands: str = "q\n", env=None):
    return subprocess.run(
        [str(binary)],
        cwd=ROOT,
        input=commands,
        text=True,
        capture_output=True,
        timeout=10,
        env=env,
        check=True,
    )


@pytest.fixture(scope="module")
def pm_stub():
    subprocess.run(
        [
            "make",
            "-C",
            "src/stub",
            "build",
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


def test_pm_nvm_namespace_is_disjoint_from_v8_dimmer_state():
    text = (ROOT / "src/device_config/nvm_items.h").read_text()
    assert "NV_ITEM_MIGRATION_MARKER    40" in text
    assert "NV_ITEM_RELAY_BINDING_INTENT(relay_idx)    (41 + (relay_idx))" in text
    assert "NV_ITEM_SWITCH_BINDING_COMMAND_MODE(switch_idx)    (46 + (switch_idx))" in text
    assert "NV_ITEM_ENERGY_ACCUMULATION(endpoint)    (64 + (endpoint) - 1)" in text
    assert "NV_ITEM_ENERGY_CALIBRATION               68" in text
    assert "NV_ITEM_OVERLOAD_CONFIG                  69" in text


def test_legacy_pm_state_migrates_byte_exactly_and_sources_are_preserved(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)

    energy = struct.pack("<Q", 12345)
    calibration = struct.pack("<IIII", 0x484C5743, 161460, 144679, 16989)
    overload = struct.pack("<8H", 2100, 10000, 5, 26000, 18000, 30, 3680, 16000)

    # Historical PM fork: decimal IDs 40, 44, 51.
    _item(40).write_bytes(energy)
    _item(44).write_bytes(calibration)
    _item(51).write_bytes(overload)

    result = _run(pm_stub)
    assert "PM NVM migration: preserved legacy energy" in result.stdout
    assert "PM NVM migration: preserved legacy calibration" in result.stdout
    assert "PM NVM migration: preserved legacy overload config" in result.stdout

    # Unified V8 namespace: decimal IDs 64, 68, 69.
    assert _item(64).read_bytes() == energy
    assert _item(68).read_bytes() == calibration
    assert _item(69).read_bytes() == overload

    # Migration is copy-only: retain historical bytes for rollback/forensics.
    assert _item(40).read_bytes() == energy
    assert _item(44).read_bytes() == calibration
    assert _item(51).read_bytes() == overload


def test_unified_destination_wins_and_migration_is_idempotent(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)

    legacy_energy = struct.pack("<Q", 111)
    unified_energy = struct.pack("<Q", 999)
    _item(40).write_bytes(legacy_energy)
    _item(64).write_bytes(unified_energy)

    _run(pm_stub)
    assert _item(64).read_bytes() == unified_energy
    _run(pm_stub)
    assert _item(64).read_bytes() == unified_energy
    assert _item(40).read_bytes() == legacy_energy


def test_non_bseed_identity_never_imports_legacy_pm_items(pm_stub):
    _clean_nvm()
    _write_device_config(OTHER_CONFIG)
    _item(40).write_bytes(struct.pack("<Q", 777))

    _run(pm_stub)
    assert not _item(64).exists()
    assert _item(40).read_bytes() == struct.pack("<Q", 777)


def test_pm_target_short_config_gets_meter_clusters_without_nvm_config_rewrite(pm_stub):
    _clean_nvm()
    _write_device_config(PM_CONFIG)
    before = _item(2).read_bytes()

    result = _run(
        pm_stub,
        "zcl_read 1 0b04 0505\n"  # Electrical Measurement / rmsVoltage
        "zcl_read 1 0702 0000\n"  # Metering / currentSummDelivered
        "q\n",
    )
    assert "Config: implicit b28wrpvx BL0937 meter CF=A1 CF1=C2 SEL=B1" in result.stdout
    assert "attr_not_found ep=1 cluster=0x0B04" not in result.stdout
    assert "attr_not_found ep=1 cluster=0x0702" not in result.stdout
    assert _item(2).read_bytes() == before


def test_generic_v8_build_does_not_enable_implicit_bseed_metering():
    assert GENERIC_BIN.exists(), "make tests must build the generic stub first"
    _clean_nvm()
    _write_device_config(PM_CONFIG)

    result = _run(
        GENERIC_BIN,
        "zcl_read 1 0b04 0505\n"
        "zcl_read 1 0702 0000\n"
        "q\n",
    )
    assert "Config: implicit b28wrpvx BL0937 meter" not in result.stdout
    assert "attr_not_found ep=1 cluster=0x0B04" in result.stdout
    assert "attr_not_found ep=1 cluster=0x0702" in result.stdout


def test_proven_bseed_no_load_filter_is_before_energy_accumulation():
    header = (ROOT / "src/base_components/energy_measurement/hlw8012.h").read_text()
    source = (ROOT / "src/base_components/energy_measurement/hlw8012.c").read_text()

    assert "HLW8012_NO_LOAD_POWER_W              2" in header
    assert "HLW8012_NO_LOAD_CURRENT_MA           50" in header
    assert "HLW8012_NO_LOAD_CONFIRM_SAMPLES      3" in header

    suppression = source.index("dev->data.no_load_suppressed = 1")
    accumulation = source.index("dev->data.energy_acc +=")
    assert suppression < accumulation
    assert "if (!dev->data.no_load_suppressed)" in source[suppression:accumulation + 200]
