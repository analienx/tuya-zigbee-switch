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
    assert "if (zb_isDeviceFactoryNew() || !zb_isDeviceJoinedNwk())" in body
    assert "if (zb_isDeviceFactoryNew() ||" in body
    assert "telink_zigbee_hal_router_ota_in_progress()" in body
    assert "ROUTER_RECOVERY_STALL_MS" in body
    assert "router_reboot_scheduled" in body
    assert "schedule_reboot(2000)" in body
    assert "schedule_network_reset" not in body
    assert "zb_factoryReset" not in body
    assert "zb_rejoinReqWithBackOff" not in body


def test_telink_superloop_services_router_watchdog_without_clients():
    main = TELINK_MAIN.read_text(encoding="utf-8")
    header = TELINK_HEADER.read_text(encoding="utf-8")
    assert "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n        telink_zigbee_hal_router_recovery_tick();\n#endif" in main
    assert "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE" in header
    assert "void telink_zigbee_hal_router_recovery_tick(void);" in header


def test_routers_probe_real_coordinator_reachability_before_secure_rejoin():
    source = TELINK.read_text(encoding="utf-8")
    assert "zb_zdoNodeDescReq(0x0000u" in source
    assert "router_coordinator_node_desc_response" in source
    assert "rsp->status == ZDO_SUCCESS" in source
    assert "rsp->nwk_addr_interest == 0x0000u" in source
    assert "ROUTER_PROBE_FAILURE_THRESHOLD" in source
    assert "ROUTER_PROBE_REJECT_THRESHOLD" in source
    assert "ROUTER_REJECT_QUIET_MS" in source
    assert "router_probe_sequence" in source
    assert "rsp->seq_num == router_probe_sequence" in source
    assert "ROUTER_QUIET_MIN_MS" in source
    assert "router_probe_failures >= ROUTER_PROBE_FAILURE_THRESHOLD" in source
    assert "ROUTER_REJOIN_COOLDOWN_MS" in source
    assert "zb_rejoinSecModeSet(REJOIN_SECURITY)" in source
    assert "zb_rejoinReq(zb_apsChannelMaskGet()" in source
    assert "bdb_networkSteerStart" not in source.split(
        "void telink_zigbee_hal_router_recovery_tick(void)", 1
    )[1].split("#endif", 1)[0]


def test_coordinator_heartbeat_is_low_frequency_and_cancels_on_inbound():
    source = TELINK.read_text(encoding="utf-8")
    assert "ROUTER_PROBE_INTERVAL_MS" in source
    assert "ROUTER_PROBE_DEADLINE_MS" in source
    assert "router_probe_pending" in source
    assert "router_probe_failures = 0" in source
    assert "router_last_positive_ms = hal_millis()" in source
    assert "telink_zigbee_hal_router_note_inbound" in source
    zcl = Path("src/telink/hal/zigbee_zcl.c").read_text(encoding="utf-8")
    assert "telink_zigbee_hal_router_note_inbound();" in zcl


def test_router_recovery_pauses_during_ota_and_expires_stale_ownership():
    network = TELINK.read_text(encoding="utf-8")
    ota = Path("src/telink/hal/zigbee_ota.c").read_text(encoding="utf-8")
    assert "telink_zigbee_hal_router_ota_in_progress()" in network
    assert "IMAGE_UPGRADE_STATUS_NORMAL" in ota
    assert "zcl_attr_imageUpgradeStatus != IMAGE_UPGRADE_STATUS_NORMAL" in ota
    assert "ROUTER_OTA_ACTIVITY_LEASE_MS" in ota
    assert "router_ota_in_progress = true" in ota
    assert "router_ota_in_progress = false" in ota
    assert "evt == OTA_EVT_IMAGE_DONE || evt == OTA_EVT_COMPLETE" in ota
    assert "schedule_network_reset(" not in network.split(
        "void telink_zigbee_hal_router_recovery_tick(void)", 1
    )[1].split("#endif", 1)[0]


def test_ts0726_has_explicit_distinct_candidate_and_exact_sha_ci():
    script = Path("make_scripts/build_bseed_ts0726_v8.sh").read_text(encoding="utf-8")
    ci = Path(".github/workflows/bseed-ts0726-router-recovery.yml").read_text(
        encoding="utf-8"
    )
    assert "router-recovery-candidate" in script
    assert "1.1.9-bseedrv1" in script
    assert "0x11023010" in script
    assert "BSEED_TS0726_RELEASE_CHANNEL: router-recovery-candidate" in ci
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"' in ci
    assert "bseed_ota_identity.py gate" in ci


def test_inbound_neighbor_zcl_does_not_spoof_coordinator_reachability():
    source = TELINK.read_text(encoding="utf-8")
    inbound = source.split("void telink_zigbee_hal_router_note_inbound(void) {", 1)[1].split(
        "static void router_coordinator_node_desc_response", 1
    )[0]
    assert "router_last_inbound_ms = hal_millis()" in inbound
    assert "router_probe_failures = 0" not in inbound
    assert "router_probe_pending = false" not in inbound
    assert "router_last_positive_ms = hal_millis()" not in inbound
    assert "router_last_positive_ms = hal_millis()" in source.split(
        "static void router_coordinator_node_desc_response", 1
    )[1].split("/* Last resort", 1)[0]


def test_isolation_rescue_requires_many_failed_probes_and_earlier_rejoins():
    source = TELINK.read_text(encoding="utf-8")
    method = source.split("void telink_zigbee_hal_router_recovery_tick(void) {", 1)[1].split(
        "#endif", 1
    )[0]
    assert "ROUTER_ISOLATED_REBOOT_MS" in method
    assert "ROUTER_INBOUND_GRACE_MS" in method
    assert "router_one_shot_attempts >= ROUTER_MIN_REJOIN_ATTEMPTS" in method
    assert "router_last_inbound_ms" in method
    assert "router_reboot_scheduled = true" in method
    assert "router_one_shot_active" in method
    assert "repeated_response_timeouts || stuck_request_queue" in method


def test_sdk_joins_rearm_device_announce_even_without_join_state_edge():
    net = TELINK.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    header = Path("src/app.h").read_text(encoding="utf-8")
    callback = net.split("void bdb_commissioning_callback(u8 status, void *arg) {", 1)[1]
    success = callback.split("case BDB_COMMISSION_STA_SUCCESS:", 1)[1].split("break;", 1)[0]
    assert "app_rearm_network_announcement();" in success
    assert "void app_rearm_network_announcement(void)" in app
    assert "announce_retry_pending = false" in app.split(
        "void app_rearm_network_announcement(void)", 1
    )[1].split("}", 1)[0]
    assert "void app_rearm_network_announcement(void);" in header


def test_router_watchdog_never_instruments_coordinator_or_client_role():
    for path in (TELINK, TELINK_HEADER, TELINK_MAIN,
                 Path("src/telink/hal/zigbee_ota.c"),
                 Path("src/telink/hal/zigbee_zcl.c")):
        source = path.read_text(encoding="utf-8")
        assert "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE" in source
    assert "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE" in TELINK.read_text(
        encoding="utf-8"
    )
