#!/usr/bin/env python3
"""Fail-closed static audit for the authoritative BSEED TS0726 v4 overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TARGET_MODEL = 'model: "EC-GL86ZPCS31"'
TARGET_BUILD = 'softwareBuildID: "1.1.4-bseedv4"'
TARGET_FP_PARTS = (
    'manufacturerName: "iedhxgyi"',
    'modelID: "TS0726-3-BS"',
    TARGET_BUILD,
    "priority: 100",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("converter", type=Path)
    args = parser.parse_args()

    text = args.converter.read_text(encoding="utf-8")
    failures: list[str] = []

    if text.count(TARGET_MODEL) != 1:
        failures.append(f"target model count != 1: {text.count(TARGET_MODEL)}")
    for part in TARGET_FP_PARTS:
        if text.count(part) != 1:
            failures.append(f"fingerprint marker count != 1 for {part!r}: {text.count(part)}")
    if "zigbeeModel:" in text:
        failures.append("authoritative v4 overlay must not contain zigbeeModel fallback")

    for marker in (
        'logicalOnOff(["relay_left", "relay_middle", "relay_right"])',
        '"Logical relay state"',
        '"Logical state after power-up"',
        '"Physical relay behavior"',
        "recommended for smart bulbs/dimmers",
        "immediately switch mains power",
        "persists across restart",
        "relay_1 = Left, relay_2 = Middle, relay_3 = Right",
        '"Button type"',
        '"Button command behavior"',
        '"Local relay trigger"',
        '"Bound-device trigger"',
        '"Long-press threshold"',
        '"Hold dimming speed"',
        '"Indicator LED behavior"',
        '"Indicator LED state"',
        '"Last button action"',
        '"Advanced hardware configuration (read-only)"',
        'access: "STATE_GET"',
        "Firmware migration owns this",
        "may require recovery firmware",
        "legacyActionEvent()",
        'prefix: "switch_0"',
        'prefix: "switch_1"',
        'prefix: "switch_2"',
        "action: prefix + \"_\" + suffix",
        'configure: async () => {}',
        "configureReporting: false",
    ):
        if marker not in text:
            failures.append(f"missing marker: {marker}")

    # No deployment-time topology mutation is allowed from this overlay.
    for forbidden in (
        "reporting.bind(",
        ".bind(",
        ".unbind(",
        ".configureReporting(",
    ):
        if forbidden in text:
            failures.append(f"forbidden topology mutation surface present: {forbidden}")

    for prop in (
        "relay_left_physical_mode",
        "relay_middle_physical_mode",
        "relay_right_physical_mode",
    ):
        if text.count(f'physicalRelayMode("{prop}"') != 1:
            failures.append(f"{prop}: expected exactly one physicalRelayMode call")

    # UX order is part of the target contract. Advanced raw pin mapping stays last.
    order = [
        'logicalOnOff(["relay_left", "relay_middle", "relay_right"])',
        'physicalRelayMode("relay_left_physical_mode"',
        'buttonType("switch_left_mode"',
        'indicatorBehavior("relay_left_indicator_mode"',
        'lastButtonAction("switch_left_press_action"',
        'deviceConfigReadOnly("device_config"',
    ]
    positions = [text.find(marker) for marker in order]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        failures.append(f"unexpected expose hierarchy: {list(zip(order, positions))}")

    output = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "softwareBuildID": "1.1.4-bseedv4",
        "targetModelCount": text.count(TARGET_MODEL),
        "physicalModeCalls": {
            prop: text.count(f'physicalRelayMode("{prop}"')
            for prop in (
                "relay_left_physical_mode",
                "relay_middle_physical_mode",
                "relay_right_physical_mode",
            )
        },
        "rawHardwareConfigReadOnly": (
            '"Advanced hardware configuration (read-only)"' in text
            and 'access: "STATE_GET"' in text
        ),
        "zeroConfigureMutationSurface": not any(
            marker in text
            for marker in (
                "reporting.bind(",
                ".bind(",
                ".unbind(",
                ".configureReporting(",
            )
        ),
    }
    print(json.dumps(output, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
