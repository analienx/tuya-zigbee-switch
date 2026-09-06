from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_pm_prefix_length_is_tc32_portable():
    source = (ROOT / "src/device_config/pm_legacy_migration.c").read_text(
        encoding="utf-8"
    )
    assert "const size_t prefix_len" not in source
    assert "const uint16_t prefix_len" in source
    assert "sizeof(PM_IDENTITY_PREFIX) - 1u" in source


def test_gpio_counter_does_not_link_against_private_gpio_translation_unit_symbol():
    source = (ROOT / "src/telink/hal/gpio_counter.c").read_text(encoding="utf-8")
    assert "extern GPIO_PullTypeDef hal_to_telink_pull" not in source
    assert "static GPIO_PullTypeDef counter_to_telink_pull" in source
    assert "counter_to_telink_pull(pull)" in source


def test_telink_libc_polyfills_supply_strstr_used_by_common_parser():
    source = (ROOT / "src/telink/libc_polyfills/atoi.c").read_text(encoding="utf-8")
    assert "char *strstr(const char *haystack, const char *needle)" in source


def test_reproducibility_workflow_checks_out_real_pr_head_and_keeps_logs_external():
    workflow = (
        ROOT / ".github/workflows/pm-reproducibility.yml"
    ).read_text(encoding="utf-8")
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert 'tee "$RUNNER_TEMP/pm-validator-output.log"' in workflow
    assert "${{ runner.temp }}/pm-validator-output.log" in workflow
    assert "toolchain/tc32/bin/tc32-elf-gcc" in workflow
