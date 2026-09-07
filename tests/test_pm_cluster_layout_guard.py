import shutil
import struct
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NVM_DIR = ROOT / "stub_nvm_data"
GENERIC_BIN = ROOT / "build" / "stub" / "stub_device"
MULTI_SWITCH_METER_CONFIG = "test;PM-LAYOUT;SD0u;SD1u;EPA1C2B1;M;"


def _clean_nvm() -> None:
    shutil.rmtree(NVM_DIR, ignore_errors=True)
    NVM_DIR.mkdir(parents=True, exist_ok=True)


def _write_device_config(config: str) -> None:
    raw = config.encode("ascii")
    assert len(raw) < 128
    (NVM_DIR / "item_02.bin").write_bytes(
        struct.pack("<H", len(raw)) + raw.ljust(128, b"\0")
    )


def test_ep1_meter_clusters_are_final_before_later_endpoint_pointer_allocation():
    source = (ROOT / "src/device_config/config_parser.c").read_text(
        encoding="utf-8"
    )
    layout = source.split("endpoints[0].clusters = cluster_ptr;", 1)[1]
    meter = layout.index(
        "electrical_measurement_cluster_add_to_endpoint(&elec_meas_cluster,"
    )
    switch_loop = layout.index(
        "for (int index = 0; index < switch_clusters_cnt; index++)"
    )
    assert meter < switch_loop


def test_explicit_meter_does_not_overwrite_second_switch_endpoint_clusters():
    assert GENERIC_BIN.exists(), "make tests must build the generic stub first"
    _clean_nvm()
    _write_device_config(MULTI_SWITCH_METER_CONFIG)

    result = subprocess.run(
        [str(GENERIC_BIN)],
        cwd=ROOT,
        input=(
            "machine on\n"
            "zcl_read 1 0b04 0505\n"  # EP1 Electrical Measurement
            "zcl_read 1 0702 0000\n"  # EP1 Metering
            "zcl_read 2 0007 0000\n"  # EP2 Switch Configuration / switchType
            "q\n"
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )

    assert "Config: explicit pulse meter" in result.stdout
    assert "RES OK ep=1 cluster=0x0B04 attr=0x0505" in result.stdout
    assert "RES OK ep=1 cluster=0x0702 attr=0x0000" in result.stdout
    assert "RES OK ep=2 cluster=0x0007 attr=0x0000" in result.stdout
    assert "RES ERR attr_not_found ep=2 cluster=0x0007" not in result.stdout

    _clean_nvm()
