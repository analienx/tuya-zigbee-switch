"""Contract checks for cross-role Router reconnection hardening.

These are source-level guards, not substitutes for SDK state-machine and
hardware power-isolation / route-failure canary evidence.
"""

from pathlib import Path

APP = Path("src/app.c")
TELINK = Path("src/telink/hal/zigbee_network.c")
TELINK_MAIN = Path("src/telink/main.c")
TELINK_HEADER = Path("src/telink/hal/telink_zigbee_hal.h")
SILABS = Path("src/silabs/hal/zigbee.c")


def test_announcement_rearmed_for_all_roles_and_retried_on_local_failure():
    source = APP.read_text(encoding="utf-8")
    body = source.split("void app_task()", 1)[1]
    assert "if (network_status != HAL_ZIGBEE_NETWORK_JOINED)" in body
    assert "boot_announce_sent = false" in body
    assert "ANNOUNCE_RETRY_MS" in body
    assert "hal_millis() - last_announce_attempt_ms" in body
    assert "hal_zigbee_send_announce() == HAL_ZIGBEE_OK" in body
    assert body.index("hal_zigbee_send_announce() == HAL_ZIGBEE_OK") < body.index(
        "boot_announce_sent = true"
    )
    assert "schedule_network_reset" not in body


def test_silabs_rejected_start_does_not_fake_joining():
    source = SILABS.read_text(encoding="utf-8")
    method = source.split("void hal_zigbee_start_network_steering()", 1)[1].split(
        "// Network steering complete callback", 1
    )[0]
    assert "SL_ZIGBEE_JOINED_NETWORK" in method
    assert "network_steering_retry_pending" in method
    assert "sl_zigbee_af_network_steering_start()" in method
    assert "if (status != SL_STATUS_OK)" in method
    assert "network_steering_in_progress = false" in method


def test_telink_router_rescue_never_resets_factory_new_or_joined():
    source = TELINK.read_text(encoding="utf-8")
    body = source.split("void telink_zigbee_hal_router_recovery_tick(void)", 1)[
        1
    ].split("#endif", 1)[0]
    assert "zb_isDeviceJoinedNwk() || zb_isDeviceFactoryNew()" in body
    assert "ROUTER_RECOVERY_STALL_MS" in body
    assert "router_reboot_scheduled" in body
    assert "schedule_reboot(2000)" in body
    assert "schedule_network_reset" not in body
    assert "zb_factoryReset" not in body
    assert "zb_rejoinReqWithBackOff" not in body


def test_telink_superloop_services_router_watchdog_without_clients():
    main = TELINK_MAIN.read_text(encoding="utf-8")
    header = TELINK_HEADER.read_text(encoding="utf-8")
    assert "#if ZB_ROUTER_ROLE\n        telink_zigbee_hal_router_recovery_tick();\n#endif" in main
    assert "#if ZB_ROUTER_ROLE" in header
    assert "void telink_zigbee_hal_router_recovery_tick(void);" in header
