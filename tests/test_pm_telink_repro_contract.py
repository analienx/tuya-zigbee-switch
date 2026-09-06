from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_pm_prefix_length_is_tc32_portable():
    source = (ROOT / "src/device_config/pm_legacy_migration.c").read_text(
        encoding="utf-8"
    )
    assert "const size_t prefix_len" not in source
    assert "const uint16_t prefix_len" in source
    assert "sizeof(PM_IDENTITY_PREFIX) - 1u" in source


def test_reproducibility_workflow_checks_out_real_pr_head_and_keeps_logs_external():
    workflow = (
        ROOT / ".github/workflows/pm-reproducibility.yml"
    ).read_text(encoding="utf-8")
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert 'tee "$RUNNER_TEMP/pm-validator-output.log"' in workflow
    assert "${{ runner.temp }}/pm-validator-output.log" in workflow
    assert "toolchain/tc32/bin/tc32-elf-gcc" in workflow
