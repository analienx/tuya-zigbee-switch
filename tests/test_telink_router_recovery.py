"""Regression guards for Telink Router network-recovery state separation."""

from pathlib import Path


SOURCE = Path("src/telink/hal/zigbee_network.c")


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_telink_tracks_steering_and_rejoin_as_distinct_recovery_states() -> None:
    source = _source()
    assert "TELINK_NETWORK_RECOVERY_STEERING" in source
    assert "TELINK_NETWORK_RECOVERY_REJOIN" in source
    assert "network_recovery_state" in source


def test_active_rejoin_is_reported_as_joining_to_block_app_resteering() -> None:
    source = _source()
    status_fn = source.split(
        "hal_zigbee_network_status_t hal_zigbee_get_network_status(void)", 1
    )[1].split("void hal_register_on_network_status_change_callback", 1)[0]
    assert "network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE" in status_fn
    assert "return HAL_ZIGBEE_NETWORK_JOINING" in status_fn


def test_known_network_uses_rejoin_instead_of_fresh_bdb_steering() -> None:
    source = _source()
    steering_fn = source.split("void hal_zigbee_start_network_steering(void)", 1)[
        1
    ].split("hal_zigbee_status_t hal_zigbee_send_announce", 1)[0]
    assert "if (!zb_isDeviceFactoryNew())" in steering_fn
    assert "start_rejoin_with_backoff" in steering_fn
    assert "bdb_networkSteerStart" in steering_fn
    assert steering_fn.index("start_rejoin_with_backoff") < steering_fn.index(
        "bdb_networkSteerStart"
    )


def test_rejoin_start_is_idempotent_and_never_used_for_factory_new_device() -> None:
    source = _source()
    rejoin_fn = source.split("static bool start_rejoin_with_backoff(void)", 1)[
        1
    ].split("static void notify_about_network_status_change", 1)[0]
    assert "zb_isDeviceFactoryNew()" in rejoin_fn
    assert "network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE" in rejoin_fn
    assert "zb_rejoinReqWithBackOff" in rejoin_fn
    assert "network_recovery_state = TELINK_NETWORK_RECOVERY_REJOIN" in rejoin_fn


def test_no_scan_response_is_not_grouped_with_parent_lost_rejoin() -> None:
    source = _source()
    callback = source.split("void bdb_commissioning_callback", 1)[1].split(
        "void bdb_identify_callback", 1
    )[0]
    no_scan_pos = callback.index("case BDB_COMMISSION_STA_NO_SCAN_RESPONSE:")
    parent_lost_pos = callback.index("case BDB_COMMISSION_STA_PARENT_LOST:")
    rejoin_pos = callback.index("start_rejoin_with_backoff", parent_lost_pos)
    assert no_scan_pos < parent_lost_pos < rejoin_pos
    assert "network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE" in callback[
        no_scan_pos:parent_lost_pos
    ]
    assert "start_rejoin_with_backoff" not in callback[no_scan_pos:parent_lost_pos]


def test_parent_loss_and_rejoin_failure_use_rejoin_recovery() -> None:
    source = _source()
    callback = source.split("void bdb_commissioning_callback", 1)[1].split(
        "void bdb_identify_callback", 1
    )[0]
    parent_block = callback.split("case BDB_COMMISSION_STA_PARENT_LOST:", 1)[1].split(
        "case BDB_COMMISSION_STA_REJOIN_FAILURE:", 1
    )[0]
    rejoin_failure_block = callback.split(
        "case BDB_COMMISSION_STA_REJOIN_FAILURE:", 1
    )[1].split("default:", 1)[0]
    assert "start_rejoin_with_backoff" in parent_block
    assert "!zb_isDeviceFactoryNew()" in rejoin_failure_block
    assert "start_rejoin_with_backoff" in rejoin_failure_block


def test_join_success_clears_recovery_state_for_future_failures() -> None:
    source = _source()
    status_fn = source.split(
        "hal_zigbee_network_status_t hal_zigbee_get_network_status(void)", 1
    )[1].split("void hal_register_on_network_status_change_callback", 1)[0]
    joined_block = status_fn.split("if (zb_isDeviceJoinedNwk())", 1)[1].split(
        "if (network_recovery_state", 1
    )[0]
    assert "network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE" in joined_block
