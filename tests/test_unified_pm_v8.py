import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC_BIN = ROOT / "src/stub/stub"
PM_CONFIG = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"


def _run(binary: Path, commands: str):
    return subprocess.run([str(binary)], input=commands, text=True, capture_output=True, check=True, cwd=ROOT)


def _clean_nvm():
    nvm = ROOT / "stub_nvm.bin"
    if nvm.exists():
        nvm.unlink()


def _write_device_config(config: str):
    # Existing stub helper accepts the config through the persisted NVM fixture path.
    subprocess.run([str(GENERIC_BIN)], input=f"config {config}\nq\n", text=True, capture_output=True, cwd=ROOT)


def test_pm_config_contract_is_present_in_source_tree():
    parser = (ROOT / "src/device_config/config_parser.c").read_text(encoding="utf-8")
    assert "BSEED_PM_B28WRPVX" in parser
    assert "TS011F-BS-PM" in parser


def test_recovery_pm_semantics_are_predecessor_compatible():
    header = (ROOT / "src/base_components/energy_measurement/hlw8012.h").read_text()
    source = (ROOT / "src/base_components/energy_measurement/hlw8012.c").read_text()

    # The recovery candidate intentionally removes the V8-only no-load filter
    # and restores predecessor SEL startup/energy accumulation semantics.
    assert "HLW8012_NO_LOAD_POWER_W" not in header
    assert "HLW8012_NO_LOAD_CURRENT_MA" not in header
    assert "HLW8012_NO_LOAD_CONFIRM_SAMPLES" not in header
    assert "no_load_suppressed" not in source
    assert "no_load_samples" not in source
    assert "hal_gpio_init(sel_pin, 0, HAL_GPIO_PULL_NONE);" in source
    assert "hal_gpio_set(sel_pin);" in source
    assert "dev->data.energy_acc +=" in source


def test_recovery_keeps_meter_cluster_sources_present():
    assert (ROOT / "src/zigbee/electrical_measurement_cluster.c").is_file()
    assert (ROOT / "src/zigbee/metering_cluster.c").is_file()
