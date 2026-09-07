"""Release-contract checks for the BSEED v6/v7 direct-binding policy split."""

from pathlib import Path


def _define_value(text: str, name: str) -> str:
    """Return a preprocessor define value without depending on alignment."""
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0] == "#define" and parts[1] == name:
            return parts[2]
    raise AssertionError(f"missing #define {name}")


def test_custom_binding_attribute_uses_next_free_switch_config_id() -> None:
    consts = Path("src/zigbee/consts.h").read_text(encoding="utf-8")
    assert _define_value(
        consts, "ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_BINDING_MODE"
    ) == "0xff05"
    assert _define_value(
        consts, "ZCL_ATTR_ONOFF_CONFIGURATION_BINDING_COMMAND_MODE"
    ) == "0xff06"


def test_binding_policy_nvm_range_is_disjoint_from_existing_v5_state() -> None:
    nvm = Path("src/device_config/nvm_items.h").read_text(encoding="utf-8")
    assert _define_value(nvm, "NV_ITEM_MIGRATION_MARKER") == "40"
    assert _define_value(
        nvm, "NV_ITEM_RELAY_BINDING_INTENT(relay_idx)"
    ) == "(41 + (relay_idx))"
    assert _define_value(
        nvm, "NV_ITEM_SWITCH_BINDING_COMMAND_MODE(switch_idx)"
    ) == "(46 + (switch_idx))"
    # Five relay-intent slots are 41..45; five switch-policy slots are 46..50.
    assert 41 + 4 < 46


def test_standard_and_extended_action_storage_are_separate() -> None:
    header = Path("src/zigbee/switch_cluster.h").read_text(encoding="utf-8")
    source = Path("src/zigbee/switch_cluster.c").read_text(encoding="utf-8")
    assert "uint8_t              action;" in header
    assert "uint8_t              binding_command_mode;" in header
    assert "attr_infos[9]" in header
    assert "ZCL_ATTR_ONOFF_CONFIGURATION_BINDING_COMMAND_MODE" in source
    assert "switch (cluster->binding_command_mode)" in source
    assert "switch (cluster->action)" in source


def test_legacy_action_values_are_migrated_not_reinterpreted_on_wire() -> None:
    source = Path("src/zigbee/switch_cluster.c").read_text(encoding="utf-8")
    assert "requested_action <=\n            ZCL_ONOFF_CONFIGURATION_BINDING_COMMAND_MAX" in source
    assert "cluster->binding_command_mode = requested_action;" in source
    assert "cluster->action =\n                ZCL_ONOFF_CONFIGURATION_SWITCH_ACTION_TOGGLE_SIMPLE;" in source


def test_disabled_bound_mode_has_explicit_zero_abi() -> None:
    consts = Path("src/zigbee/consts.h").read_text(encoding="utf-8")
    assert _define_value(
        consts, "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_DISABLED"
    ) == "0x00"
    assert _define_value(
        consts, "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_RISE"
    ) == "0x01"
    assert _define_value(
        consts, "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_LONG"
    ) == "0x02"
    assert _define_value(
        consts, "ZCL_ONOFF_CONFIGURATION_BINDED_MODE_SHORT"
    ) == "0x03"


def test_disabled_bound_mode_suppresses_onoff_and_level_transmission() -> None:
    source = Path("src/zigbee/switch_cluster.c").read_text(encoding="utf-8")
    guard = "cluster->binded_mode == ZCL_ONOFF_CONFIGURATION_BINDED_MODE_DISABLED"

    # The guard is deliberately inside the common OnOff binding-action helper,
    # so it also covers toggle switch mode, where callers invoke binding action
    # unconditionally.
    binding_action = source.split("static void switch_cluster_binding_action(", 1)[1].split(
        "// Send OnOff command to bound device", 1
    )[0]
    assert guard in binding_action
    assert binding_action.index(guard) < binding_action.index("switch (cluster->binding_command_mode)")

    # Long-press dimming used to bypass binded_mode entirely. Disabled must
    # suppress both Move and Stop even if an old Level binding still exists.
    level_stop = source.split("void switch_cluster_level_stop(", 1)[1].split(
        "void switch_cluster_level_control(", 1
    )[0]
    level_control = source.split("void switch_cluster_level_control(", 1)[1].split(
        "void switch_cluster_on_button_press(", 1
    )[0]
    assert guard in level_stop
    assert guard in level_control
    assert level_stop.index(guard) < level_stop.index("hal_zigbee_has_binding")
    assert level_control.index(guard) < level_control.index("hal_zigbee_has_binding")
