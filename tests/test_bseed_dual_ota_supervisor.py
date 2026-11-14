"""Parallel OTA never triggers automatic source rejoin or repeated flashing."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_ota_resume_supervisor import validate_dual_ota_supervisor_policy, dual_ota_reentry_blocked  # noqa:E402


def test_singleton_preserves_existing_supervisor_semantics():
    validate_dual_ota_supervisor_policy({},12)
    validate_dual_ota_supervisor_policy({"parallel_ota_max":1},5)


def test_dual_requires_one_shot_and_explicit_nonintrusive_policy():
    spec={"parallel_ota_max":2,"source_rejoin_strategy":"none","allow_join_all_fallback":False}
    assert validate_dual_ota_supervisor_policy(spec,1) is None
    with pytest.raises(ValueError,match="max-attempts 1"):
        validate_dual_ota_supervisor_policy(spec,3)
    with pytest.raises(ValueError,match="source_rejoin_strategy=none"):
        validate_dual_ota_supervisor_policy({"parallel_ota_max":2},1)
    with pytest.raises(ValueError,match="source_rejoin_strategy=none"):
        validate_dual_ota_supervisor_policy({**spec,"source_rejoin_strategy":"auto"},1)
    with pytest.raises(ValueError,match="permit-join-all"):
        validate_dual_ota_supervisor_policy({**spec,"allow_join_all_fallback":True},1)


def test_existing_dual_campaign_never_enters_rejoin_or_retry_path():
    first={"parallel_ota_max":2}
    assert dual_ota_reentry_blocked(first,None) is False
    for phase in ("preflight_complete","ota_running","update_error",
                  "update_timeout_or_unconfirmed","source_unchanged_reconciled",
                  "ota_transfer_ok_postflash_unverified","postflash_candidate"):
        assert dual_ota_reentry_blocked(first,{"phase":phase}) is True
    assert dual_ota_reentry_blocked({"parallel_ota_max":1},{"phase":"update_error"}) is False
