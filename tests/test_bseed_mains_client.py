from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_client_role_is_always_awake_non_router():
    client_make = (ROOT / "src/telink/client.mk").read_text()
    assert "-DEND_DEVICE=1" in client_make
    assert "-DBSEED_MAINS_CLIENT=1" in client_make
    assert "-DZB_MAC_RX_ON_WHEN_IDLE=1" in client_make
    assert "-lzb_ed" in client_make
    assert "PM_ENABLE" not in client_make
    assert "-DROUTER" not in client_make
    assert "-lzb_router" not in client_make


def test_sleepy_end_device_path_remains_separate():
    makefile = (ROOT / "src/telink/Makefile").read_text()
    assert "ifeq ($(DEVICE_TYPE), end_device)" in makefile
    assert "-DEND_DEVICE=1 -DMCU_STARTUP_8258=1 -DPM_ENABLE" in makefile
    assert "-lzb_ed" in makefile


def test_mains_client_does_not_enable_sleep_or_polling():
    main = (ROOT / "src/telink/main.c").read_text()
    network = (ROOT / "src/telink/hal/zigbee_network.c").read_text()
    assert "#if PM_ENABLE" in main
    assert "defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)" in network
    assert "af_nodeDescRxOnWhenIdleUpdate(1);" in network
    assert "POWER_MODE_RECEIVER_SYNCHRONIZED_WHEN_ON_IDLE" in network
    assert ".available_power_sources    = POWER_SRC_MAINS_POWER" in network
    assert ".current_power_source       = POWER_SRC_MAINS_POWER" in network


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


def test_client_artifacts_are_separate_and_never_stock_or_auto_indexed():
    script = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()
    assert "CLIENT_IMAGE_TYPE=65024" in script
    assert "CLIENT_IMAGE_TYPE=65025" in script
    assert "0xFE00" in script
    assert "0xFE01" in script
    assert "ROUTER_IMAGE_TYPE=43556" in script
    assert "ROUTER_IMAGE_TYPE=45577" in script
    assert "from-router.ota" in script
    assert "from_tuya" not in script.lower()
    assert "make_z2m_ota_index" not in script
    assert '"normalOtaIndex": False' in script
    assert '"stockConversion": False' in script


def test_production_router_build_scripts_stay_router_only():
    pm = (ROOT / "make_scripts/build_bseed_ts011f_pm_v8.sh").read_text()
    dimmer = (ROOT / "make_scripts/build_bseed_ts0726_v8.sh").read_text()
    assert "DEVICE_TYPE=router" in pm
    assert "IMAGE_TYPE=43556" in pm
    assert "FILE_VERSION_HEX='0x12053006'" in pm
    assert "DEVICE_TYPE=router" in dimmer
    assert "IMAGE_TYPE=45577" in dimmer
    assert "FILE_VERSION_HEX='0x1102300A'" in dimmer
