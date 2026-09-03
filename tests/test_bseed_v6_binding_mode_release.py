"""Release-contract checks for the BSEED v6 direct-binding policy split."""

from pathlib import Path


def test_custom_binding_attribute_uses_next_free_switch_config_id() -> None:
    consts = Path("src/zigbee/consts.h").read_text(encoding="utf-8")
    assert "ZCL_ATTR_ONOFF_CONFIGURATION_SWITCH_BINDING_MODE       0xff05" in consts
    assert "ZCL_ATTR_ONOFF_CONFIGURATION_BINDING_COMMAND_MODE      0xff06" in consts


def test_binding_policy_nvm_range_is_disjoint_from_existing_v5_state() -> None:
    nvm = Path("src/device_config/nvm_items.h").read_text(encoding="utf-8")
    assert "NV_ITEM_MIGRATION_MARKER    40" in nvm
    assert "NV_ITEM_RELAY_BINDING_INTENT(relay_idx)    (41 + (relay_idx))" in nvm
    assert "NV_ITEM_SWITCH_BINDING_COMMAND_MODE(switch_idx)    (46 + (switch_idx))" in nvm
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
