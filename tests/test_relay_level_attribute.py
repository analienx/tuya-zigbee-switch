"""Regression: relay Level Control server must answer CurrentLevel reads."""

from tests.conftest import Device, RelayButtonPair
from tests.zcl_consts import (
    ZCL_ATTR_LEVEL_CURRENT_LEVEL,
    ZCL_ATTR_ONOFF,
    ZCL_CLUSTER_LEVEL_CONTROL,
    ZCL_CLUSTER_ON_OFF,
    ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZCL_CMD_ONOFF_OFF,
    ZCL_CMD_ONOFF_ON,
    ZCL_CMD_ONOFF_TOGGLE,
)


def test_binary_relay_level_is_readable_and_tracks_commands(
    device: Device, relay_button_pairs: list[RelayButtonPair]
) -> None:
    for pair in relay_button_pairs:
        ep = pair.relay_endpoint
        for cmd, level, on_off in (
            (ZCL_CMD_ONOFF_OFF, "0", "0"),
            (ZCL_CMD_ONOFF_ON, "254", "1"),
            (ZCL_CMD_ONOFF_TOGGLE, "0", "0"),
        ):
            device.call_zigbee_cmd(ep, ZCL_CLUSTER_ON_OFF, cmd)
            assert device.read_zigbee_attr(ep, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_LEVEL_CURRENT_LEVEL) == level
            assert device.read_zigbee_attr(ep, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF) == on_off
        for requested, expected_level in ((1, "254"), (127, "254"), (0, "0")):
            device.call_zigbee_cmd(
                ep,
                ZCL_CLUSTER_LEVEL_CONTROL,
                ZCL_CMD_LEVEL_MOVE_TO_LEVEL_WITH_ON_OFF,
                payload=bytes((requested, 0, 0)),
            )
            assert device.read_zigbee_attr(
                ep, ZCL_CLUSTER_LEVEL_CONTROL, ZCL_ATTR_LEVEL_CURRENT_LEVEL
            ) == expected_level



def test_switch_input_endpoints_remain_level_clients_only(
    device: Device, relay_button_pairs: list[RelayButtonPair]
) -> None:
    for pair in relay_button_pairs:
        # Buttons send Level Control commands to bound targets. They must not
        # pretend to serve a local brightness attribute of their own.
        reply = device.p.exec(
            f"zcl_read {pair.switch_endpoint} 0x0008 0x0000"
        )
        assert not reply.ok
        assert "attr_not_found" in reply.payload.get("error", "") or not reply.ok
