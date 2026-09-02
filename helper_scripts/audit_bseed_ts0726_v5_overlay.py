#!/usr/bin/env python3
"""Fail-closed static audit for the BSEED TS0726 v5 overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TARGET_MODEL = 'model: "EC-GL86ZPCS31"'
TARGET_BUILD = 'softwareBuildID: "1.1.5-bseedv5"'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("converter", type=Path)
    args = parser.parse_args()

    text = args.converter.read_text(encoding="utf-8")
    failures: list[str] = []

    for marker in (
        TARGET_MODEL,
        'manufacturerName: "iedhxgyi"',
        'modelID: "TS0726-3-BS"',
        TARGET_BUILD,
        "priority: 100",
    ):
        if text.count(marker) != 1:
            failures.append(f"exact matcher marker count != 1 for {marker!r}")

    if "zigbeeModel:" in text:
        failures.append("v5 overlay must not contain a bare zigbeeModel fallback")

    required = (
        'channelLabel(feature.endpoint || expose.endpoint) + " — Logical state"',
        'channelLabel(endpointName) + " — Mains power"',
        'channelLabel(endpointName) + " — LED shows"',
        "logical_state: 0",
        "inverse_logical_state: 1",
        "manual: 2",
        "physical_output: 3",
        "binding_status: 4",
        'channelLabel(endpointName) + " — Bound light (tracked)"',
        "not remote-state confirmation",
        "sends no command",
        "changes no binding",
        '.withLabel("Advanced — Hardware configuration")',
        '.withLabel("Advanced — Enable editing")',
        '.withEndpoint("advanced")',
        "advanced: 1",
        'exposes.enum("device_config_unlock", ea.SET, ["enable_editing"])',
        "DEVICE_CONFIG_UNLOCK_MS = 60_000",
        "requireDeviceConfigUnlock(meta)",
        "deviceConfigUnlocks.delete(unlockKey)",
        'tokens[0] !== "iedhxgyi"',
        'tokens[1] !== "TS0726-3-BS"',
        "switches.length !== 3",
        "relays.length !== 3",
        "indicators.length !== 3",
        "GPIO pin(s) assigned more than once",
        ".text(name, ea.ALL)",
        "DEVICE_CONFIG_CHUNK_MAX = 24",
        '"deviceConfigStage"',
        '"deviceConfigCommit"',
        "crc16CcittFalse",
        "recovery firmware",
        "legacyActionEvent()",
        "configure: async () => {}",
        "configureReporting: false",
    )
    for marker in required:
        if marker not in text:
            failures.append(f"missing marker: {marker}")

    for forbidden in (
        "reporting.bind(",
        ".bind(",
        ".unbind(",
        ".configureReporting(",
    ):
        if forbidden in text:
            failures.append(f"forbidden topology mutation surface: {forbidden}")

    config_start = text.find("const deviceConfigEditable")
    config_end = text.find("const legacyActionEvent")
    if config_start < 0 or config_end <= config_start:
        failures.append("cannot isolate editable device_config converter")
        config_block = ""
    else:
        config_block = text[config_start:config_end]
        if ".write(" in config_block:
            failures.append("device_config SET still contains direct attribute write")
        if '.read("genBasic", [0xff00]' not in config_block:
            failures.append("device_config GET no longer reads legacy Basic 0xff00")

    order = [
        'logicalOnOff(["relay_left", "relay_middle", "relay_right"])',
        'physicalRelayMode("relay_left_physical_mode"',
        'buttonType("switch_left_mode"',
        'indicatorBehavior("relay_left_indicator_mode"',
        'lastButtonAction("switch_left_press_action"',
        "deviceConfigUnlock()",
        'deviceConfigEditable("device_config"',
    ]
    positions = [text.find(marker) for marker in order]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        failures.append(f"unexpected expose hierarchy: {list(zip(order, positions))}")

    output = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "softwareBuildID": "1.1.5-bseedv5",
        "indicatorEnumAbi": {
            "logical_state": 0,
            "inverse_logical_state": 1,
            "manual": 2,
            "physical_output": 3,
            "binding_status": 4,
        },
        "deviceConfig": {
            "editable": ".text(name, ea.ALL)" in config_block,
            "clickToUnlock": "requireDeviceConfigUnlock(meta)" in config_block,
            "unlockWindowMs": 60_000,
            "boardStructureValidation": "switches.length !== 3" in text and "GPIO pin(s) assigned more than once" in text,
            "chunkSize": 24,
            "directAttributeWrite": ".write(" in config_block,
            "legacyReadProperty": '.read("genBasic", [0xff00]' in config_block,
        },
        "channelLabelsSelfIdentify": all(
            marker in text
            for marker in (
                'switch_left: "Left"',
                'switch_middle: "Middle"',
                'switch_right: "Right"',
                'relay_left: "Left"',
                'relay_middle: "Middle"',
                'relay_right: "Right"',
            )
        ),
        "advancedEndpointAlias": "advanced: 1" in text,
        "zeroTopologyMutationSurface": not any(
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
