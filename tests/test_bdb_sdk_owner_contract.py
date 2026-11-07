"""Pin live SDK operation attribution claims to Telink V3.7.2.0 source.

This checks what the shipped SDK actually emits, not an invented callback
token. Downloaded by .github/workflows/test.yml on hosted Actions only.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"
PINNED = ROOT / "build/test-sdk/bdb.c"


def test_pinned_bdb_rejoin_completion_has_no_operation_identifier():
    source = PINNED.read_text(encoding="utf-8")
    start = source.index("void bdb_zdoStartDevCnf(")
    finish = source.index("case BDB_STATE_INIT:", start)
    idle = source[start:finish]
    assert "case BDB_STATE_IDLE:" in idle
    assert "if (startDevCnf->status == ZDO_NETWORK_LOST)" in idle
    assert "BDB_STATUS_SET(BDB_COMMISSION_STA_PARENT_LOST);" in idle
    assert "BDB_STATUS_SET(BDB_COMMISSION_STA_SUCCESS);" in idle
    assert "evt = BDB_STATE_REJOIN_DONE;" in idle
    assert "TL_SCHEDULE_TASK(bdb_task, (void *)evt);" in idle
    confirm = source[source.index("static u8 bdb_topLevelCommissiongConfirm(void)\n{"):]
    confirm = confirm[:confirm.index("\n}", 2)]
    assert "bdbcommissioningCb(g_bdbAttrs.commissioningStatus, NULL)" in confirm
    assert "operation_id" not in confirm and "generation" not in confirm


def test_owner_release_requires_actual_join_observation_on_sdk_success():
    source = NETWORK.read_text(encoding="utf-8")
    callback = source.split("void bdb_commissioning_callback(u8 status, void *arg) {", 1)[1]
    success = callback.split("case BDB_COMMISSION_STA_SUCCESS:", 1)[1]
    success = success.split("case BDB_COMMISSION_STA_IN_PROGRESS:", 1)[0]
    assert "zb_isDeviceJoinedNwk()" in success
    assert "Unverified BDB SUCCESS; retaining SDK radio owner" in success
    assert success.index("Unverified BDB SUCCESS") < success.index(
        "network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE")
    init = source.split("void bdb_init_callback(u8 status, u8 joinedNetwork) {", 1)[1]
    init = init.split("void bdb_commissioning_callback", 1)[0]
    assert init.index("if (sdk_owner_live)") < init.index(
        "network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE")
