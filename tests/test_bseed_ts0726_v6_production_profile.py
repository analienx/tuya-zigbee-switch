"""Production-profile contract for the BSEED TS0726 V6 controller."""

from pathlib import Path


OVERLAY = Path("zigbee2mqtt/converters/bseed_ts0726_v5.js")


def _source() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def _bound_trigger_block(js: str) -> str:
    start = js.index("const boundDeviceTrigger")
    end = js.index("const longPressThreshold", start)
    return js[start:end]


def test_bound_light_control_has_explicit_disabled_mode() -> None:
    block = _bound_trigger_block(_source())
    assert '"Never (disabled)": 0' in block
    assert '"On press": 1' in block
    assert '"Long press": 2' in block
    assert '"Short press": 3' in block
    assert "sends no direct-binding command" in block


def test_disabled_bound_control_is_distinct_from_local_state_control() -> None:
    js = _source()
    bound = _bound_trigger_block(js)
    local_start = js.index("const localRelayTrigger")
    local_end = js.index("const localRelayIndex", local_start)
    local = js[local_start:local_end]

    assert 'attribute: {ID: 0xff05, type: 0x30}' in bound
    assert 'attribute: {ID: 0xff01, type: 0x30}' in local
    assert '"Never (disabled)": 0' in bound
    assert '"Never (detached)": 0' in local


def test_production_right_profile_is_representable_without_raw_writes() -> None:
    js = _source()
    assert '"Follow logical state": 0' in js
    assert '"Physical output": 3' in js
    assert 'lookup: {Left: 1, Middle: 2, Right: 3}' in js
    assert 'boundDeviceTrigger("switch_right_binded_mode", "switch_right")' in js
