from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_client_role_is_always_awake_non_router():
    client_make = (ROOT / "src/telink/client.mk").read_text()
    assert "-DEND_DEVICE=1" in client_make
    assert "-DBSEED_MAINS_CLIENT=1" in client_make
    assert "-DZB_MAC_RX_ON_WHEN_IDLE=1" in client_make
    assert "-DDEBOUNCE_DELAY_MS=20" in client_make
    assert "-lzb_ed" in client_make
    assert "PM_ENABLE" not in client_make
    assert "-DROUTER" not in client_make
    assert "-lzb_router" not in client_make


def test_sleepy_end_device_path_remains_separate():
    makefile = (ROOT / "src/telink/Makefile").read_text()
    assert "ifeq ($(DEVICE_TYPE), end_device)" in makefile
    assert "-DEND_DEVICE=1 -DMCU_STARTUP_8258=1 -DPM_ENABLE" in makefile
    assert "-lzb_ed" in makefile


def test_mains_client_does_not_enable_sleep_or_poll_control():
    main = (ROOT / "src/telink/main.c").read_text()
    network = (ROOT / "src/telink/hal/zigbee_network.c").read_text()
    poll_header = (ROOT / "src/zigbee/poll_control_cluster.h").read_text()
    poll_impl = (ROOT / "src/zigbee/poll_control_cluster.c").read_text()
    assert "#if PM_ENABLE" in main
    assert "defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)" in network
    assert "af_nodeDescRxOnWhenIdleUpdate(1);" in network
    assert "POWER_MODE_RECEIVER_SYNCHRONIZED_WHEN_ON_IDLE" in network
    assert ".available_power_sources    = POWER_SRC_MAINS_POWER" in network
    assert ".current_power_source       = POWER_SRC_MAINS_POWER" in network
    assert "#if defined(END_DEVICE) && !defined(BSEED_MAINS_CLIENT)" in poll_impl
    assert "Mains clients are end devices only in the topology sense" in poll_header
    assert "static inline void poll_control_cluster_update(void)" in poll_header


def test_client_short_press_defaults_are_faster_and_press_start():
    button_header = (ROOT / "src/base_components/button.h").read_text()
    app = (ROOT / "src/app.c").read_text()
    assert "#ifndef DEBOUNCE_DELAY_MS" in button_header
    assert "apply_mains_client_defaults" in app
    assert "NV_ITEM_SWITCH_CLUSTER_DATA(i)" in app
    assert "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_RISE" in app
    assert "if (st == HAL_NVM_SUCCESS)" in app
    assert "continue;" in app


def test_client_keeps_exact_same_direct_binding_state_machine_as_router():
    switch = (ROOT / "src/zigbee/switch_cluster.c").read_text()
    # Role-specific binding forks are precisely what this experiment must avoid.
    assert "BSEED_MAINS_CLIENT" not in switch
    assert "hal_zigbee_has_binding(cluster->endpoint, ZCL_CLUSTER_ON_OFF)" in switch
    assert "hal_zigbee_send_cmd_to_bindings(&c)" in switch
    assert "switch_cluster_apply_binding_intent(cluster, cmd_id);" in switch
    assert "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_RISE" in switch
    assert "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_SHORT" in switch
    assert "ZCL_CLUSTER_LEVEL_CONTROL" in switch


def test_router_to_client_transition_resets_network_not_application_nv_module():
    app = (ROOT / "src/app.c").read_text()
    telink = (ROOT / "src/telink/hal/system.c").read_text()
    assert "stored_device_type != CURRENT_DEVICE_TYPE" in app
    assert "#ifdef BSEED_MAINS_CLIENT\nstatic bool process_device_type_change" in app
    assert "hal_role_change_reset()" in app
    assert app.index("if (process_device_type_change())") < app.index("parse_config();")
    assert "#else\nvoid process_device_type_change()" in app
    assert "#ifndef BSEED_MAINS_CLIENT\n    process_device_type_change();" in app
    assert "hal_factory_reset();" in app
    assert "hal_nvm_clear_all" not in app
    assert "#ifdef BSEED_MAINS_CLIENT\nbool hal_role_change_reset" in telink
    for module in ["NV_MODULE_ZB_INFO", "NV_MODULE_ADDRESS_TABLE", "NV_MODULE_APS", "NV_MODULE_ZCL", "NV_MODULE_OTA", "NV_MODULE_KEYPAIR"]:
        assert module in telink
    reset_body = telink.split("bool hal_role_change_reset(void)", 1)[1]
    assert "NV_MODULE_APP" not in reset_body
    assert "NV_MODULE_NWK_FRAME_COUNT" not in reset_body


def test_client_artifacts_are_separate_and_never_stock_or_auto_indexed():
    script = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()
    assert "CLIENT_IMAGE_TYPE=65024" in script
    assert "CLIENT_IMAGE_TYPE=65025" in script
    assert "ROUTER_IMAGE_TYPE=43556" in script
    assert "ROUTER_IMAGE_TYPE=45577" in script
    assert "FILE_VERSION_HEX='0x12053010'" in script
    assert "FILE_VERSION_HEX='0x1102300C'" in script
    assert "from-router.ota" in script
    assert "from_tuya" not in script.lower()
    assert "make_z2m_ota_index" not in script
    assert '"normalOtaIndex": False' in script
    assert '"stockConversion": False' in script
    assert '"pollControlCluster": False' in script
    assert '"pmEnabled": target == "pm"' in script
    assert '"defaultDebounceMs": 20' in script
    assert '"permitJoinBeforeCanary": True' in script
    assert '"zigbeeFactoryResetOnRouterToClient": False' in script
    assert '"selectiveZigbeeNvResetOnRoleChange": True' in script
    assert 'git_output("status", "--porcelain", "--untracked-files=no")' in script


def test_mains_client_recovers_periodic_ota_query_after_abort():
    ota = (ROOT / "src/telink/hal/zigbee_ota.c").read_text()
    assert '#include "hal/tasks.h"' in ota
    assert "static hal_task_t ota_abort_query_retry_task;" in ota
    assert "hal_tasks_init(&ota_abort_query_retry_task);" in ota
    assert "ota_abort_query_retry_task.handler = ota_abort_query_retry;" in ota
    assert "ota_abort_query_retry_task.arg     = NULL;" in ota
    assert "hal_tasks_unschedule(&ota_abort_query_retry_task);" in ota
    assert "hal_tasks_schedule(&ota_abort_query_retry_task," in ota
    assert "OTA_ABORT_QUERY_RETRY_DELAY_MS" in ota
    assert "ota_queryStart(OTA_PERIODIC_QUERY_INTERVAL);" in ota
    assert "#ifdef BSEED_OTA_DEFERRED_REQUERY" in ota


def test_production_router_build_scripts_stay_router_only():
    pm = (ROOT / "make_scripts/build_bseed_ts011f_pm_v8.sh").read_text()
    dimmer = (ROOT / "make_scripts/build_bseed_ts0726_v8.sh").read_text()
    assert "DEVICE_TYPE=router" in pm
    assert "IMAGE_TYPE=43556" in pm
    assert "FILE_VERSION_HEX='0x12053007'" in pm
    assert "DEVICE_TYPE=router" in dimmer
    assert "IMAGE_TYPE=45577" in dimmer
    assert "FILE_VERSION_HEX='0x1102300A'" in dimmer
