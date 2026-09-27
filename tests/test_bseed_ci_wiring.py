"""Offline CI wiring audit: checkout SHA, matrix/sealer chain, historical separation.

No workflow is executed here; public GitHub Actions at the exact source SHA
remains the gate of record.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
HEAD_REF = "${{ github.event.pull_request.head.sha || github.sha }}"

# build.yml is the legacy dispatch-only SiLabs lane and is exempt from the
# candidate head-pin rules below.
CANDIDATE_WORKFLOWS = sorted(
    p.name for p in WORKFLOWS.glob("*.yml") if p.name != "build.yml"
)
NATIVE_BUILD_WORKFLOWS = [
    "bseed-pm-matrix.yml",
    "telink-router-ci.yml",
    "bseed-mains-client-experimental.yml",
    "bseed-nonpm-client-canary.yml",
    "bseed-ts0726-currentlevel-canary.yml",
]


def load_workflow(name):
    with open(WORKFLOWS / name, encoding="utf8") as handle:
        return yaml.safe_load(handle)


def test_all_workflows_parse_with_triggers_and_jobs():
    names = sorted(p.name for p in WORKFLOWS.glob("*.yml"))
    assert len(names) >= 9
    for name in names:
        document = load_workflow(name)
        assert document.get("name"), name
        assert document.get(True) is not None, name  # YAML 1.1 `on:` key
        assert document.get("jobs"), name


def test_no_elevated_or_reusable_trigger_modes():
    # Audited: no pull_request_target or workflow_call; adding either must
    # extend this audit deliberately.
    for name in CANDIDATE_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf8")
        assert "pull_request_target" not in text, name
        assert "workflow_call" not in text, name


def test_candidate_checkouts_pin_exact_head_not_merge_commit():
    for name in CANDIDATE_WORKFLOWS:
        document = load_workflow(name)
        checked = 0
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if "actions/checkout@" in uses:
                    checked += 1
                    assert step.get("with", {}).get("ref") == HEAD_REF, (name, step)
        assert checked >= 1, name


def test_native_builds_guard_exact_head_before_build():
    for name in NATIVE_BUILD_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf8")
        assert "EXPECTED_HEAD" in text, name
        assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"' in text, name


def test_no_bare_assert_in_workflow_policy():
    for name in CANDIDATE_WORKFLOWS:
        for lineno, line in enumerate(
            (WORKFLOWS / name).read_text(encoding="utf8").splitlines(), 1
        ):
            assert not re.match(r"\s*assert\s", line), (name, lineno, line)


def test_artifact_names_use_candidate_head_not_merge_sha():
    for name in ("bseed-pm-matrix.yml", "test.yml", "bseed-nonpm-client-canary.yml",
                 "bseed-mains-client-experimental.yml",
                 "bseed-ts0726-currentlevel-canary.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf8")
        assert "${{ github.sha }}" not in text, name


def test_native_evidence_artifacts_carry_candidate_head():
    # Every evidence upload in the native candidate lanes must bind to the
    # exact head SHA so artifacts from different jobs/ShAs cannot be mixed.
    # test.yml failure diagnostics and the version-named historical lanes
    # (ota-distribution, pm-reproducibility) are deliberately exempt.
    for name in NATIVE_BUILD_WORKFLOWS:
        document = load_workflow(name)
        checked = 0
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                if "actions/upload-artifact@" in step.get("uses", ""):
                    checked += 1
                    artifact = step.get("with", {}).get("name", "")
                    assert HEAD_REF in artifact, (name, artifact)
        assert checked >= 1, name


def test_sealer_inputs_are_produced_by_candidate_workflows():
    seal = (ROOT / "helper_scripts/bseed_pm_seal.py").read_text(encoding="utf8")
    assert "'--nonpm-dir'" in seal or '"--nonpm-dir"' in seal
    pm_matrix = (WORKFLOWS / "bseed-pm-matrix.yml").read_text(encoding="utf8")
    assert "build/bseed-pm-role-matrix-*/ROLE_MATRIX.json" in pm_matrix
    assert "build/bseed-pm-role-matrix-*/router/forward.ota" in pm_matrix
    assert "build/bseed-pm-role-matrix-*/client/forward.ota" in pm_matrix
    assert "CLIENT_RETURN.json" in pm_matrix
    canary = (WORKFLOWS / "bseed-nonpm-client-canary.yml").read_text(encoding="utf8")
    assert "build/bseed-nonpm-role-matrix-*/ROLE_MATRIX.json" in canary
    assert "build/bseed-nonpm-role-matrix-*/router/*" in canary
    assert "build/bseed-nonpm-role-matrix-*/client/*" in canary


def test_matrix_helpers_build_both_fresh_roles():
    pm = (ROOT / "helper_scripts/bseed_pm_variant_matrix.py").read_text(encoding="utf8")
    assert "build_bseed_ts011f_pm_v8.sh" in pm
    assert "build_bseed_mains_client.sh" in pm
    assert "BSEED_PM_CONSOLIDATED" in pm
    nonpm = (ROOT / "helper_scripts/bseed_nonpm_variant_matrix.py").read_text(encoding="utf8")
    assert "build_bseed_ts011f_nonpm_router.sh" in nonpm
    assert "build_bseed_mains_client.sh" in nonpm


def test_historical_verification_does_not_rebuild_old_identities():
    for name in ("bseed-mains-client-experimental.yml", "bseed-nonpm-client-canary.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf8")
        assert "make bseed/identity-gate" in text, name
        assert "git worktree add" not in text, name
