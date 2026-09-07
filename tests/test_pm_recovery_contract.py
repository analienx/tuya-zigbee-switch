from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HLW_C = ROOT / "src/base_components/energy_measurement/hlw8012.c"
HLW_H = ROOT / "src/base_components/energy_measurement/hlw8012.h"
BUILD = ROOT / "make_scripts/build_bseed_ts011f_pm_v8.sh"


def test_recovery_restores_predecessor_sampling_semantics():
    c = HLW_C.read_text(encoding="utf-8")
    h = HLW_H.read_text(encoding="utf-8")

    assert "hal_gpio_init(sel_pin, 0, HAL_GPIO_PULL_NONE);" in c
    assert "hal_gpio_set(sel_pin);" in c
    assert "hal_gpio_init_output(sel_pin" not in c

    assert "no_load_suppressed" not in c
    assert "no_load_samples" not in c
    assert "HLW8012_NO_LOAD_" not in h

    assert "dev->data.energy_acc +=" in c
    assert "if (!dev->data.no_load_suppressed)" not in c


def test_recovery_keeps_v8_platform_guards():
    c = HLW_C.read_text(encoding="utf-8")
    assert "cf_pin == HAL_INVALID_PIN" in c
    assert "HAL_GPIO_COUNTER_INVALID" in c
    assert "hal_gpio_counter_deinit" in c


def test_recovery_identity_is_strictly_newer_than_failed_v8():
    text = BUILD.read_text(encoding="utf-8")
    assert "SW_BUILD='1.2.5-bseed-pm-recovery1'" in text
    assert "FILE_VERSION_HEX='0x12053003'" in text
    assert "FILE_VERSION_DEC=302329859" in text
    assert "MANUFACTURER_CODE=4417" in text
    assert "IMAGE_TYPE=43556" in text
    assert "CANONICAL='b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'" in text
    assert "VOLTAGE_MULTIPLIER=161460" in text
    assert "CURRENT_MULTIPLIER=144679" in text
    assert "POWER_MULTIPLIER=16989" in text
