#!/usr/bin/env python3
"""Fail-closed static audit for a target-only BSEED TS0726 canary converter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TARGET_FP = '{ manufacturerName: "iedhxgyi", modelID: "TS0726-3-BS", softwareBuildID: "1.1.4-8542fc05", priority: 100 }'
TARGET_MODEL = 'model: "EC-GL86ZPCS31"'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("converter", type=Path)
    args = parser.parse_args()

    text = args.converter.read_text(encoding="utf-8")
    failures: list[str] = []

    if text.count(TARGET_MODEL) != 1:
        failures.append(f"expected one EC-GL86ZPCS31 definition, got {text.count(TARGET_MODEL)}")
    if 'model: "EC-SL-FK86ZPCS31"' in text:
        failures.append("sibling TS0726 definition leaked into target-only overlay")
    if text.count(TARGET_FP) != 1:
        failures.append(f"target fingerprint count != 1: {text.count(TARGET_FP)}")

    bare = [
        line.strip()
        for line in text.splitlines()
        if '"TS0726-3-BS"' in line and "manufacturerName" not in line
    ]
    if bare:
        failures.append(f"bare TS0726-3-BS matcher(s): {bare}")

    for marker in (
        "bseedTargetOnOff",
        'feature.withLabel("Logical relay state")',
        "does not necessarily switch mains power",
        'expose.withLabel("Logical state after power-up")',
        "Physical relay behavior is independent",
        "relay_1 = Left, relay_2 = Middle, relay_3 = Right",
        'lookup: { follow_state: 0, always_on: 1, always_off: 2 }',
        'label: "Physical relay behavior"',
        "Recommended for smart bulbs",
        "Changing this setting can immediately switch mains power",
        "Advanced hardware configuration",
        "may require recovery firmware",
        'softwareBuildID: "1.1.4-8542fc05"',
        "priority: 100",
    ):
        if marker not in text:
            failures.append(f"missing marker: {marker}")

    # Target top-level configure is a no-op and onOff does not configure
    # reporting. Helper source elsewhere in the file may contain reporting.bind
    # for generic code paths, therefore inspect only the target definition.
    model_pos = text.find(TARGET_MODEL)
    start = text.rfind("\n    {\n", 0, model_pos)
    end = text.find("\n    },\n", model_pos)
    target = text[start:end] if start >= 0 and end >= 0 else ""
    if not target:
        failures.append("cannot isolate target definition")
    else:
        if "bseedTargetOnOff" not in target:
            failures.append("target does not use bseedTargetOnOff")
        if "reporting.bind(" in target:
            failures.append("target contains direct reporting.bind")
        if "reporting.onOff(" in target:
            failures.append("target contains direct reporting.onOff")
        configure = target[target.find("configure: async") :]
        if "await " in configure:
            failures.append("target top-level configure contains await/mutation code")

        # Intended conceptual order.
        markers = [
            "bseedTargetOnOff",
            'romasku.relayPhysicalMode("relay_left_physical_mode"',
            'romasku.switchMode("switch_left_mode"',
            'romasku.relayIndicatorMode("relay_left_indicator_mode"',
            'romasku.pressAction("switch_left_press_action"',
            'romasku.deviceConfig("device_config"',
        ]
        positions = [target.find(m) for m in markers]
        if any(p < 0 for p in positions) or positions != sorted(positions):
            failures.append(f"target expose hierarchy unexpected: {list(zip(markers, positions))}")

    print(
        json.dumps(
            {
                "status": "PASS" if not failures else "FAIL",
                "failures": failures,
                "target_fingerprint_count": text.count(TARGET_FP),
                "bare_target_matchers": bare,
            },
            indent=2,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
