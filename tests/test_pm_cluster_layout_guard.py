import re
import shutil
import struct
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NVM_DIR = ROOT / "stub_nvm_data"
GENERIC_BIN = ROOT / "build" / "stub" / "stub_device"
MULTI_SWITCH_METER_CONFIG = "test;PM-LAYOUT;SD0u;SD1u;EPA1C2B1;M;"
# Fleet-proven PM scaling: voltage 1/100, current 1/1000, power 1/1,
# metering 1/1000. Without these Z2M shows raw values (voltage 100x).
PM_SCALING_READS = (
    ("0b04", "0600", "1"),
    ("0b04", "0601", "100"),
    ("0b04", "0602", "1"),
    ("0b04", "0603", "1000"),
    ("0b04", "0604", "1"),
    ("0b04", "0605", "1"),
    # UINT24 values print little-endian hex in the stub.
    ("0702", "0301", "01 00 00"),  # multiplier = 1
    ("0702", "0302", "e8 03 00"),  # divisor = 1000
)


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


def test_ep1_pm_electrical_scaling_attributes_are_registered():
    electrical = (ROOT / "src/zigbee/electrical_measurement_cluster.c").read_text(
        encoding="utf-8"
    )
    for symbol in (
        "ZCL_ATTR_ELEC_MEAS_AC_VOLTAGE_MULTIPLIER",
        "ZCL_ATTR_ELEC_MEAS_AC_VOLTAGE_DIVISOR",
        "ZCL_ATTR_ELEC_MEAS_AC_CURRENT_MULTIPLIER",
        "ZCL_ATTR_ELEC_MEAS_AC_CURRENT_DIVISOR",
        "ZCL_ATTR_ELEC_MEAS_AC_POWER_MULTIPLIER",
        "ZCL_ATTR_ELEC_MEAS_AC_POWER_DIVISOR",
    ):
        assert re.search(
            rf"SETUP_ATTR\(\d+,\s*{symbol},\s*ZCL_DATA_TYPE_UINT16,\s*ATTR_READONLY",
            electrical,
        ), f"{symbol}: missing or incorrectly typed read attribute"
    metering = (ROOT / "src/zigbee/metering_cluster.c").read_text(encoding="utf-8")
    for symbol in (
        "ZCL_ATTR_METERING_MULTIPLIER",
        "ZCL_ATTR_METERING_DIVISOR",
    ):
        assert re.search(
            rf"SETUP_ATTR\(\d+,\s*{symbol},\s*ZCL_DATA_TYPE_UINT24,\s*ATTR_READONLY",
            metering,
        ), f"{symbol}: missing or incorrectly typed read attribute"


def test_ep1_pm_scaling_attributes_read_with_proven_values():
    """0x0B04 0x0600-0x0605 and 0x0702 multiplier/divisor must read.

    Live fleet reads returned UNSUPPORTED for these, leaving Z2M on raw
    values. This proves the shared attribute-table contract; the separate
    Telink registration test guards the hardware-specific dispatch path.
    """
    assert GENERIC_BIN.exists(), "make tests must build the generic stub first"
    _clean_nvm()
    _write_device_config(MULTI_SWITCH_METER_CONFIG)

    script = "machine on\n"
    for cluster, attr, _ in PM_SCALING_READS:
        script += f"zcl_read 1 {cluster} {attr}\n"
    script += "q\n"
    result = subprocess.run(
        [str(GENERIC_BIN)],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )

    for cluster, attr, value in PM_SCALING_READS:
        assert (
            f"RES OK ep=1 cluster=0x{cluster.upper()} attr=0x{attr} value={value}"
            in result.stdout
        ), f"scaling attr {cluster}/{attr} missing or wrong"

    _clean_nvm()
