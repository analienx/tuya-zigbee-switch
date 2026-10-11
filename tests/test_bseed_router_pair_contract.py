"""Paired PM/non-PM Router release and recovery contract."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper_scripts"))

import bseed_router_pair_contract as pair


def test_paired_router_contract() -> None:
    result = pair.validate()
    assert result["families"]["pm"]["imageType"] == 43556
    assert result["families"]["nonpm"]["imageType"] == 43555
    assert result["sharedRecoveryBoardNeutral"] is True
    assert result["routerKeepaliveRoleGated"] is True


def test_telink_router_ci_builds_both_families_and_has_pair_gate() -> None:
    workflow = (ROOT / ".github/workflows/telink-router-ci.yml").read_text(encoding="utf-8")
    assert "id: pm" in workflow
    assert "script: make_scripts/build_bseed_ts011f_pm_v8.sh" in workflow
    assert "image_type: 43556" in workflow
    assert "id: nonpm" in workflow
    assert "script: make_scripts/build_bseed_ts011f_nonpm_router.sh" in workflow
    assert "image_type: 43555" in workflow
    assert "paired-router-contract:" in workflow
    assert "needs: bseed-ts011f-router" in workflow
