from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "base_components" / "energy_measurement" / "hlw8012.c"
BUILD = ROOT / "make_scripts" / "build_bseed_ts011f_pm_recovery.sh"


def test_recovery_semantics_are_bound_to_exact_successor_version():
    text = SOURCE.read_text(encoding="utf-8")
    assert "defined(BSEED_PM_B28WRPVX) && (FILE_VERSION == 0x12053003)" in text
    assert "#define BSEED_PM_RECOVERY_PREDECESSOR_SEMANTICS 1" in text


def test_recovery_restores_hardware_proven_sel_startup_only_in_recovery_path():
    text = SOURCE.read_text(encoding="utf-8")
    recovery = text.index("#ifdef BSEED_PM_RECOVERY_PREDECESSOR_SEMANTICS")
    normal = text.index("#else", recovery)
    end = text.index("#endif", normal)
    recovery_block = text[recovery:normal]
    normal_block = text[normal:end]

    assert "hal_gpio_init(sel_pin, 0, HAL_GPIO_PULL_NONE);" in recovery_block
    assert "hal_gpio_set(sel_pin);" in recovery_block
    assert "hal_gpio_init_output(sel_pin, HAL_GPIO_PULL_NONE, 1);" not in recovery_block
    assert "hal_gpio_init_output(sel_pin, HAL_GPIO_PULL_NONE, 1);" in normal_block


def test_recovery_disables_low_load_suppression_and_accumulates_every_sane_cf_sample():
    text = SOURCE.read_text(encoding="utf-8")
    split = text.index("#ifndef BSEED_PM_RECOVERY_PREDECESSOR_SEMANTICS")
    recovery = text.index("#else", split)
    end = text.index("#endif", recovery)
    normal_block = text[split:recovery]
    recovery_block = text[recovery:end]

    assert "HLW8012_NO_LOAD_POWER_W" in normal_block
    assert "no_load_suppressed" in normal_block
    assert "dev->data.energy_acc +=" in recovery_block
    assert "HLW8012_NO_LOAD_POWER_W" not in recovery_block
    assert "no_load_suppressed" not in recovery_block


def test_recovery_builder_pins_v8_identity_and_successor_version():
    text = BUILD.read_text(encoding="utf-8")
    assert "BOARD='OUTLET_BSEED_PM_TS011F'" in text
    assert "CANONICAL='b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;'" in text
    assert "MANUFACTURER_CODE=4417" in text
    assert "IMAGE_TYPE=43556" in text
    assert "SW_BUILD='1.2.5-bseed-pm-recovery1'" in text
    assert "FILE_VERSION_HEX='0x12053003'" in text
    assert "FILE_VERSION_DEC=302329859" in text
    assert "VOLTAGE_MULTIPLIER=161460" in text
    assert "CURRENT_MULTIPLIER=144679" in text
    assert "POWER_MULTIPLIER=16989" in text
    assert "V8_ACCEPTED_BASE_SHA='ded91a1fb1cdeb320d0858c8f4bcabab32bf5564'" in text
    assert "V8_ACCEPTED_OTA_SHA256='c3ccb484c28d7ef08594acc306b2054aed3ba9fcfc9579643da339f7fcc9fe7c'" in text


def test_recovery_builder_is_build_only():
    text = BUILD.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in text.lower().splitlines() if not line.lstrip().startswith("#")
    )
    assert "make -c src/telink flash" not in executable
    assert "make -c src/telink wipe" not in executable
    assert "tlsrpgm" not in executable
    assert "mqtt" not in executable
    assert "publish" not in executable
    assert "make -C src/telink build" in text
    assert "make -C src/telink ota" in text
