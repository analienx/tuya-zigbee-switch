#!/usr/bin/env python3
"""Fail-closed audit for the BSEED TS0726-3-BS canary converter.

This is intentionally text-level: it verifies the generated artifact that will
be copied into Zigbee2MQTT, not only the Python/Jinja source that produced it.

Run after:
    make tools/update_converters
or:
    python helper_scripts/make_z2m_custom_converters.py device_db.yaml \
        > zigbee2mqtt/converters/switch_custom.js

It checks both maintained converter generations by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TARGET_MODEL = "EC-GL86ZPCS31"
TARGET_MANUFACTURER = "iedhxgyi"
TARGET_ZB_MODEL = "TS0726-3-BS"
OTHER_MANUFACTURER = "r2fgo9ks"
OTHER_MODEL = "EC-SL-FK86ZPCS31"


def _definition_block(text: str, converter_model: str) -> str:
    marker = f'model: "{converter_model}"'
    model_pos = text.find(marker)
    if model_pos < 0:
        raise AssertionError(f"definition {converter_model!r} not found")

    start = text.rfind("\n    {\n", 0, model_pos)
    if start < 0:
        raise AssertionError(f"cannot find start of definition {converter_model!r}")
    start += 1

    end_marker = "\n    },\n    {"
    end = text.find(end_marker, model_pos)
    if end < 0:
        end = text.find("\n    },\n];", model_pos)
    if end < 0:
        raise AssertionError(f"cannot find end of definition {converter_model!r}")
    return text[start : end + len("\n    },")]


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    target = _definition_block(text, TARGET_MODEL)
    other = _definition_block(text, OTHER_MODEL)

    expected_target_fp = (
        f'{{ manufacturerName: "{TARGET_MANUFACTURER}", '
        f'modelID: "{TARGET_ZB_MODEL}" }}'
    )
    expected_other_fp = (
        f'{{ manufacturerName: "{OTHER_MANUFACTURER}", '
        f'modelID: "{TARGET_ZB_MODEL}" }}'
    )

    # Matching correctness.
    assert text.count(expected_target_fp) == 1, "target fingerprint must occur exactly once"
    assert text.count(expected_other_fp) == 1, "other TS0726 fingerprint must occur exactly once"

    bare_ts0726_lines = [
        line
        for line in text.splitlines()
        if f'"{TARGET_ZB_MODEL}"' in line and "manufacturerName" not in line
    ]
    assert not bare_ts0726_lines, (
        "ambiguous bare TS0726-3-BS matcher remains: "
        + " | ".join(bare_ts0726_lines[:5])
    )

    # Real mixed-alias regression: current unique model must survive while the
    # old alias TS0002-custom is fingerprinted.
    assert '"TS0002-GIR"' in text, "mixed-alias fix regressed: TS0002-GIR was dropped"

    # Human-facing physical relay UX.
    assert 'lookup: { follow_state: 0, always_on: 1, always_off: 2 }' in text
    assert 'label: "Physical relay behavior"' in text
    assert 'expose.withProperty(name)' in text, "new physical-mode property must not duplicate endpoint suffix"
    assert "Recommended for smart bulbs" in text
    assert "Changing this setting can immediately switch mains power" in text

    for endpoint in ("left", "middle", "right"):
        assert f'romasku.relayPhysicalMode("relay_{endpoint}_physical_mode"' in target

    # Stable machine-facing property names are retained.
    for property_name in (
        "relay_left_physical_mode",
        "relay_middle_physical_mode",
        "relay_right_physical_mode",
        "switch_left_relay_mode",
        "switch_left_binded_mode",
        "device_config",
    ):
        assert property_name in target, f"missing stable property {property_name}"

    # Improved labels/advanced warning.
    for label in (
        "Button type",
        "Button command behavior",
        "Local relay trigger",
        "Assigned local relay",
        "Bound-device trigger",
        "Long-press threshold",
        "Hold dimming speed",
        "Indicator LED behavior",
        "Indicator LED state",
        "Physical relay behavior",
        "Advanced hardware configuration",
    ):
        assert label in text, f"missing UX label {label!r}"

    assert "may require recovery firmware" in text
    assert "BSEED Echo Click / Scale 3-gang" in target
    assert "Romasku custom firmware" in target

    # Canary-specific configure safety. The target itself must not have direct
    # bind/reporting configure calls, and onOff must explicitly disable its
    # internal configureReporting callback.
    assert "configureReporting: false" in target
    assert "reporting.bind(" not in target
    assert "reporting.onOff(" not in target

    # The BSEED overlay must be narrow: the sibling TS0726 definition should
    # retain normal configure behavior, proving this was not disabled globally.
    assert "reporting.bind(" in other
    assert "reporting.onOff(" in other

    return {
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "target_fingerprint_count": text.count(expected_target_fp),
        "other_fingerprint_count": text.count(expected_other_fp),
        "bare_ts0726_matchers": len(bare_ts0726_lines),
        "physical_mode_property_count": text.count("_physical_mode"),
        "target_zero_direct_binds": True,
        "target_zero_direct_reporting_onoff": True,
        "mixed_alias_TS0002_GIR_present": True,
        "ux_contract": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[
            Path("zigbee2mqtt/converters/switch_custom.js"),
            Path("zigbee2mqtt/converters_v1/switch_custom.js"),
        ],
    )
    args = parser.parse_args()

    results = []
    for path in args.paths:
        if not path.exists():
            raise SystemExit(f"missing generated converter: {path}")
        results.append(audit(path))

    print(json.dumps({"status": "PASS", "files": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
