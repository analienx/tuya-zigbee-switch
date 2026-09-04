"""Production-profile contract for the BSEED TS0726 V6 controller."""

from pathlib import Path


PRODUCTION = Path("zigbee2mqtt/converters/bseed_ts0726_v6_production.js")
BASE = Path("zigbee2mqtt/converter_lib/bseed_ts0726_v56_hardened.js")


def test_production_wrapper_reuses_frozen_hardened_definition() -> None:
    js = PRODUCTION.read_text(encoding="utf-8")
    assert "require('../converter_lib/bseed_ts0726_v56_hardened.js')" in js
    assert "definitions.length !== 1" in js
    assert "module.exports = definitions" in js


def test_hardened_library_is_exact_production_base() -> None:
    # The production branch vendors the proven hardened blob at a non-auto-load
    # path so Z2M sees one definition only (the wrapper), while recovery to V5
    # stays supported by the same two-fingerprint base definition.
    base = BASE.read_text(encoding="utf-8")
    assert 'const V5_SW_BUILD = "1.1.5-bseedv5"' in base
    assert 'const V6_SW_BUILD = "1.1.6-bseedv6"' in base
    assert base.count("priority: 100") == 2
    assert "zigbeeModel:" not in base
    assert "Direct-binding command cannot verify firmware identity" in base


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
