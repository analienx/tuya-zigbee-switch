"""Production-profile contract for the BSEED TS0726 V5/V6/V7 controller."""

from pathlib import Path


PRODUCTION = Path("zigbee2mqtt/converters/bseed_ts0726_v6_production.js")
OVERLAY = Path("zigbee2mqtt/converter_lib/bseed_ts0726_v567_hardened.js")
BASE = Path("zigbee2mqtt/converter_lib/bseed_ts0726_v56_hardened.js")


def test_production_wrapper_uses_narrow_v7_overlay() -> None:
    js = PRODUCTION.read_text(encoding="utf-8")
    assert "require('../converter_lib/bseed_ts0726_v567_hardened.js')" in js
    assert "definitions.length !== 1" in js
    assert "module.exports = definitions" in js


def test_hardened_v56_library_remains_frozen_under_overlay() -> None:
    base = BASE.read_text(encoding="utf-8")
    assert 'const V5_SW_BUILD = "1.1.5-bseedv5"' in base
    assert 'const V6_SW_BUILD = "1.1.6-bseedv6"' in base
    assert base.count("priority: 100") == 2
    assert "zigbeeModel:" not in base
    assert "Direct-binding command cannot verify firmware identity" in base


def test_v7_overlay_adds_exact_fingerprint_and_v6_transport_only() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")
    assert "const V7_SW_BUILD = '1.1.7-bseedv7'" in overlay
    assert "softwareBuildID: V7_SW_BUILD" in overlay
    assert "swBuild === V6_SW_BUILD || swBuild === V7_SW_BUILD" in overlay
    assert "{attribute: {ID: 0xff06, type: 0x30}, max: 4}" in overlay
    assert ".bind(" not in overlay
    assert ".unbind(" not in overlay


def test_bound_light_control_has_explicit_disabled_mode() -> None:
    js = PRODUCTION.read_text(encoding="utf-8")
    assert "'Never (disabled)': 0" in js
    assert "'On press': 1" in js
    assert "'Long press': 2" in js
    assert "'Short press': 3" in js
    assert "sends no direct-binding command" in js
    assert "does not create, remove or rewrite bindings" in js


def test_disabled_trigger_is_endpoint_pinned_and_non_topology_mutating() -> None:
    js = PRODUCTION.read_text(encoding="utf-8")
    assert "endpointId: 1" in js
    assert "endpointId: 2" in js
    assert "endpointId: 3" in js
    assert "meta?.device?.getEndpoint?.(endpointId)" in js
    assert "[0xff05]: {value: requestedRaw, type: 0x30}" in js
    assert "endpoint.read('genOnOffSwitchCfg', [0xff05])" in js
    assert ".bind(" not in js
    assert ".unbind(" not in js


def test_bound_mode_set_publishes_authoritative_readback_not_request() -> None:
    js = PRODUCTION.read_text(encoding="utf-8")
    assert "const response = await endpoint.read('genOnOffSwitchCfg', [0xff05]);" in js
    assert "const actual = decodeBoundMode(response, name);" in js
    assert "publishing device truth" in js
    assert "return {state: {[key]: actual.value}}" in js
    assert "return {state: {[key]: value}}" not in js


def test_production_right_profile_is_representable_without_raw_operator_writes() -> None:
    base = BASE.read_text(encoding="utf-8")
    prod = PRODUCTION.read_text(encoding="utf-8")
    assert '"Follow logical state": 0' in base
    assert '"Physical output": 3' in base
    assert 'lookup: {Left: 1, Middle: 2, Right: 3}' in base
    assert "switch_right_binded_mode" in prod
    assert "'Never (disabled)': 0" in prod
