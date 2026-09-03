#!/usr/bin/env python3
"""Fail-closed audit for the BSEED TS0726 V5→V6 transition overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("converter", type=Path)
    args = parser.parse_args()
    text = args.converter.read_text(encoding="utf-8")
    failures: list[str] = []

    expected_counts = {
        'model: "EC-GL86ZPCS31"': 1,
        'manufacturerName: "iedhxgyi"': 2,
        'modelID: "TS0726-3-BS"': 2,
        'const V5_SW_BUILD = "1.1.5-bseedv5"': 1,
        'const V6_SW_BUILD = "1.1.6-bseedv6"': 1,
        "softwareBuildID: V5_SW_BUILD": 1,
        "softwareBuildID: V6_SW_BUILD": 1,
        "priority: 100": 2,
        'deviceAddCustomCluster("genBasic"': 1,
    }
    for marker, expected in expected_counts.items():
        count = text.count(marker)
        if count != expected:
            failures.append(f"{marker!r}: expected {expected}, got {count}")

    required = (
        'return {attribute: "switchActions", max: 2}',
        'return {attribute: {ID: 0xff06, type: 0x30}, max: 4}',
        "const directBindingTransport = async (meta) =>",
        "requires firmware",
        'deviceConfigEditable("device_config")',
        "deviceConfigUnlock()",
        "legacyActionEvent()",
        "configureReporting: false",
        "configure: async () => {}",
        "const pinnedEndpoint = (meta, endpointName)",
        "meta?.device?.getEndpoint?.(id)",
    )
    for marker in required:
        if marker not in text:
            failures.append(f"missing marker: {marker}")

    forbidden = (
        'deviceAddCustomCluster("genOnOffSwitchCfg"',
        "reporting.bind(",
        ".unbind(",
        ".configureReporting(",
        "zigbeeModel:",
    )
    for marker in forbidden:
        if marker in text:
            failures.append(f"forbidden marker: {marker}")

    output = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "firmwareIdentities": ["1.1.5-bseedv5", "1.1.6-bseedv6"],
        "singleDefinition": text.count('model: "EC-GL86ZPCS31"') == 1,
        "singleBasicCustomExtension": text.count('deviceAddCustomCluster("genBasic"') == 1,
        "noCustomSwitchCfgExtension": 'deviceAddCustomCluster("genOnOffSwitchCfg"' not in text,
        "v5Transport": "switchActions",
        "v6Transport": "0xff06",
        "zeroTopologyMutationSurface": not any(
            marker in text for marker in ("reporting.bind(", ".unbind(", ".configureReporting(")
        ),
    }
    print(json.dumps(output, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
