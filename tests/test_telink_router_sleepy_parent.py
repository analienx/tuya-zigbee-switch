from pathlib import Path


NETWORK = Path("src/telink/hal/zigbee_network.c")
MAKEFILE = Path("src/telink/Makefile")
CONFIG = Path("src/telink/configs/zb_config.h")


def _network_init() -> str:
    source = NETWORK.read_text(encoding="utf-8")
    return source.split("void telink_zigbee_hal_network_init(void) {", 1)[1].split(
        "void telink_zigbee_hal_bdb_init", 1
    )[0]


def test_router_advertises_mac_data_poll_keepalive_after_stack_init() -> None:
    init = _network_init()
    assert "zb_init();" in init
    assert "#if ZB_ROUTER_ROLE" in init
    assert "g_zbNIB.parentInfo = MAC_DATA_POLL_KEEPALIVE_BIT;" in init
    assert init.index("zb_init();") < init.index("g_zbNIB.parentInfo")
    assert init.index("g_zbNIB.parentInfo") < init.index("zb_zdoCbRegister")


def test_router_does_not_claim_unproven_timeout_request_keepalive() -> None:
    init = _network_init()
    assert "END_DEV_TIMEOUT_REQ_KEEPALIVE_BIT" not in init


def test_router_does_not_enable_power_management() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    router_block = makefile.split("ifeq ($(DEVICE_TYPE), router)", 1)[1].split(
        "endif", 1
    )[0]
    assert "PM_ENABLE" not in router_block


def test_sleepy_parent_fix_does_not_consume_neighbor_budget() -> None:
    config = CONFIG.read_text(encoding="utf-8")
    assert "TL_ZB_CHILD_TABLE_NUM" not in config
    assert "TL_ZB_NEIGHBOR_TABLE_NUM" not in config
