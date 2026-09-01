"""Tests for the deterministic Z2M converter fingerprint generation.

Model collisions are classified per re-review 5492467354 (gate E):
- RESOLVABLE: every claimant of a model has a distinct manufacturer -> each
  definition is pinned with a `fingerprint` on (manufacturerName, modelID).
- Deterministic merge: byte-identical definitions collapse into one.
- UNRESOLVED LEGACY: the same (manufacturer, model) tuple claimed by
  definitions with different contracts keeps the legacy bare `zigbeeModel`
  matcher and is reported - never presented as deterministic.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path("helper_scripts/make_z2m_custom_converters.py")

DEVICE_TMPL = """
{key}:
  human_name: {key}
  output: relay
  device_type: router
  stock_model_name: {model}
  stock_manufacturer_name: {manufacturer}
  stock_converter_model: {converter_model}
  tuya_module: ZT3L
  mcu_family: Telink
  mcu: TLSR8258
  config_str: {manufacturer};{model};{peripherals}
  build: {build}
"""


def _device(key, manufacturer, model, peripherals, converter_model, build="yes"):
    return DEVICE_TMPL.format(
        key=key,
        manufacturer=manufacturer,
        model=model,
        peripherals=peripherals,
        converter_model=converter_model,
        build=build,
    )


def run_generator(db_text: str) -> tuple[str, str]:
    db_file = Path("stub_nvm_data_gen") / "device_db.yaml"
    db_file.parent.mkdir(exist_ok=True)
    db_file.write_text(db_text)
    result = subprocess.run(
        [sys.executable, str(HELPER), str(db_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout, result.stderr


def test_resolvable_same_model_different_manufacturer(tmp_path: Path) -> None:
    db = _device("a", "mfg_a", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_a") + _device(
        "b", "mfg_b", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_b"
    )
    js, err = run_generator(db)

    assert '{ manufacturerName: "mfg_a", modelID: "MODEL-X" }' in js
    assert '{ manufacturerName: "mfg_b", modelID: "MODEL-X" }' in js
    # No bare zigbeeModel matcher may remain for the ambiguous model.
    bare = [
        line
        for line in js.splitlines()
        if '"MODEL-X"' in line and "manufacturerName" not in line
    ]
    assert not bare, bare
    assert "disambiguated via fingerprint" in err


def test_target_ts0726_group_maps_exactly_once(tmp_path: Path) -> None:
    db = _device(
        "target",
        "iedhxgyi",
        "TS0726-TEST",
        "LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;",
        "EC-GL86ZPCS31",
    ) + _device(
        "other",
        "r2fgo9ks",
        "TS0726-TEST",
        "LD4;SA1u;RB4;IC1;SC2u;RD2;IB5;M;",
        "EC-SL-FK86ZPCS31",
    )
    js, _ = run_generator(db)

    assert js.count('{ manufacturerName: "iedhxgyi", modelID: "TS0726-TEST" }') == 1
    assert js.count('{ manufacturerName: "r2fgo9ks", modelID: "TS0726-TEST" }') == 1
    gl = js.split('model: "EC-GL86ZPCS31"')[0]
    sl = js.split('model: "EC-SL-FK86ZPCS31"')[0]
    assert "iedhxgyi" in gl
    assert "r2fgo9ks" in sl


def test_unresolved_same_tuple_different_contract_stays_legacy(
    tmp_path: Path,
) -> None:
    db = _device(
        "legacy_a", "same_mfg", "SHARED-1", "SB1u;RC2;IC0;M;", "conv_a"
    ) + _device(
        "legacy_b", "same_mfg", "SHARED-1", "SB1u;RC2;IC0;SB7u;RC3;M;", "conv_b"
    )
    js, err = run_generator(db)

    # Legacy matcher preserved for the whole group; no false fingerprint.
    assert "fingerprint" not in js
    assert "UNRESOLVED legacy model collision" in err
    assert "legacy_a" in err and "legacy_b" in err


def test_identical_tuple_and_contract_is_merged(tmp_path: Path) -> None:
    db = _device(
        "dup_a", "same_mfg", "SHARED-2", "SB1u;RC2;IC0;M;", "conv_same"
    ) + _device("dup_b", "same_mfg", "SHARED-2", "SB1u;RC2;IC0;M;", "conv_same")
    js, err = run_generator(db)

    assert js.count("SHARED-2") == 1  # one definition, not two
    assert "Merged byte-identical definition" in err


def test_mixed_unique_current_and_colliding_old_alias_keeps_both_match_surfaces(
    tmp_path: Path,
) -> None:
    db = _device("a", "mfg_a", "MODEL-CURRENT", "SB1u;RC2;M;", "conv_a").replace(
        "config_str: mfg_a;MODEL-CURRENT;SB1u;RC2;M;",
        "config_str: mfg_a;MODEL-CURRENT;SB1u;RC2;M;\n  old_zb_models: [MODEL-OLD]",
    ) + _device(
        "b", "mfg_b", "MODEL-OTHER", "SB1u;RC2;M;", "conv_b"
    ).replace(
        "config_str: mfg_b;MODEL-OTHER;SB1u;RC2;M;",
        "config_str: mfg_b;MODEL-OTHER;SB1u;RC2;M;\n  old_zb_models: [MODEL-OLD]",
    )
    js, _ = run_generator(db)

    # MODEL-OLD is resolvable only by manufacturer fingerprint, while the
    # current MODEL-CURRENT remains a normal model-only alias on the SAME
    # definition. This is the real TS0002-GIR/TS0002-custom regression shape.
    assert '{ manufacturerName: "mfg_a", modelID: "MODEL-OLD" }' in js
    assert '"MODEL-CURRENT"' in js
    # Extract THIS definition's match surface: everything from the last
    # definition-opening brace (a 4-space '{' on its own line — fingerprint
    # entries are 12-space indented with content on the same line, so they
    # never match) up to the model line.
    head = js.split('model: "conv_a"')[0]
    block = re.split(r"\n    \{\n", head)[-1]
    assert "fingerprint:" in block
    assert '"MODEL-OLD"' in block
    assert "zigbeeModel:" in block
    assert '"MODEL-CURRENT"' in block
    assert "zigbeeModel:" in block


def test_old_zb_models_participates_in_collision(tmp_path: Path) -> None:
    db = _device("a", "mfg_a", "MODEL-NEW", "SB1u;RC2;M;", "conv_a") + _device(
        "b", "mfg_b", "MODEL-OTHER", "SB1u;RC2;M;", "conv_b"
    ).replace(
        "config_str: mfg_b;MODEL-OTHER;SB1u;RC2;M;",
        "config_str: mfg_b;MODEL-OTHER;SB1u;RC2;M;\n  old_zb_models: [MODEL-NEW]",
    )
    js, err = run_generator(db)

    assert '{ manufacturerName: "mfg_a", modelID: "MODEL-NEW" }' in js
    assert '{ manufacturerName: "mfg_b", modelID: "MODEL-NEW" }' in js
    assert "disambiguated via fingerprint" in err


def test_build_no_is_ignored(tmp_path: Path) -> None:
    db = _device("a", "mfg_a", "MODEL-X", "SB1u;RC2;M;", "conv_a") + _device(
        "b", "mfg_b", "MODEL-X", "SB1u;RC2;M;", "conv_b", build="no"
    )
    js, _ = run_generator(db)

    # The build:no device is not a claimant: the survivor keeps zigbeeModel.
    assert 'zigbeeModel: [\n            "MODEL-X",' in js
    assert "conv_b" not in js


def test_input_order_does_not_change_tuple_mapping(tmp_path: Path) -> None:
    db_ab = _device("a", "mfg_a", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_a") + _device(
        "b", "mfg_b", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_b"
    )
    db_ba = _device("b", "mfg_b", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_b") + _device(
        "a", "mfg_a", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_a"
    )
    js_ab, _ = run_generator(db_ab)
    js_ba, _ = run_generator(db_ba)

    # Reversing the input order must not change which contract each tuple
    # maps to (definition ORDER may differ; fingerprint->contract must not).
    def mapping(js: str) -> dict:
        out = {}
        for chunk in js.split('model: "')[1:]:
            name = chunk.split('"')[0]
            mfg = "mfg_a" if "mfg_a" in chunk[:400] else "mfg_b"
            out[name] = mfg
        return out

    assert mapping(js_ab) == mapping(js_ba)


def test_generation_is_reproducible(tmp_path: Path) -> None:
    db = _device("a", "mfg_a", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_a") + _device(
        "b", "mfg_b", "MODEL-X", "SB1u;RC2;IC0;M;", "conv_b"
    )
    assert run_generator(db)[0] == run_generator(db)[0]


def test_real_db_preserves_mixed_alias_and_target_ux() -> None:
    """Real DB regression + focused BSEED user-facing contract."""
    result = subprocess.run(
        [sys.executable, str(HELPER), "device_db.yaml"],
        capture_output=True,
        text=True,
        check=True,
    )
    js = result.stdout

    # MODULE_GIRIER_TS0002: unique current model must survive even though its
    # old TS0002-custom alias participates in a collision.
    assert '"TS0002-GIR"' in js

    assert js.count('{ manufacturerName: "iedhxgyi", modelID: "TS0726-3-BS" }') == 1
    assert 'lookup: { follow_state: 0, always_on: 1, always_off: 2 }' in js
    assert 'label: "Physical relay behavior"' in js
    assert "Recommended for smart bulbs" in js
    assert "Changing this setting can immediately switch mains power" in js
    assert 'withExposeLabel(text({' in js
    assert '"Advanced hardware configuration"' in js
    assert "may require recovery firmware" in js
    assert "BSEED Echo Click / Scale 3-gang" in js
    assert "Romasku custom firmware" in js

    target_before = js.split('model: "EC-GL86ZPCS31"')[0]
    target = target_before.rsplit("    {", 1)[-1] + js.split('model: "EC-GL86ZPCS31"', 1)[1].split("\n    },\n    {", 1)[0]
    assert "configureReporting: false" in target
    assert "reporting.bind(" not in target
    assert "reporting.onOff(" not in target


def test_bseed_audit_script_passes_on_fresh_generation(tmp_path: Path) -> None:
    audit = Path("helper_scripts/audit_bseed_ts0726_converter.py")
    out_v2 = tmp_path / "switch_custom.js"
    out_v1 = tmp_path / "switch_custom_v1.js"

    for path, extra_args in ((out_v2, []), (out_v1, ["--z2m-v1"])):
        result = subprocess.run(
            [sys.executable, str(HELPER), "device_db.yaml", *extra_args],
            capture_output=True,
            text=True,
            check=True,
        )
        path.write_text(result.stdout)

    result = subprocess.run(
        [sys.executable, str(audit), str(out_v2), str(out_v1)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"status": "PASS"' in result.stdout
    assert '"ux_contract": "PASS"' in result.stdout


def test_regenerated_files_match_committed_files() -> None:
    """Regeneration consistency: BOTH maintained converter files are clean."""
    result_v2 = subprocess.run(
        [sys.executable, str(HELPER), "device_db.yaml"],
        capture_output=True,
        text=True,
        check=True,
    )
    result_v1 = subprocess.run(
        [sys.executable, str(HELPER), "device_db.yaml", "--z2m-v1"],
        capture_output=True,
        text=True,
        check=True,
    )
    committed_v2 = Path("zigbee2mqtt/converters/switch_custom.js").read_text()
    committed_v1 = Path("zigbee2mqtt/converters_v1/switch_custom.js").read_text()
    assert result_v2.stdout == committed_v2
    assert result_v1.stdout == committed_v1
