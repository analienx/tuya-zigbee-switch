#!/usr/bin/env python3
"""Read an existing Z2M OTA capture OFFLINE; never opens MQTT or restarts Z2M.

Example: python helper_scripts/reparse_tongou_ota_capture.py path/to/capture.json
Prints only target-attributed OTA tuples; never echoes raw logs, addresses,
network credentials, or unrelated device traffic.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .capture_tongou_ota_tuple import extract_target_ota_tuples
else:
    from capture_tongou_ota_tuple import extract_target_ota_tuples


def reparse(path: Path) -> dict:
    capture = json.loads(path.read_text(encoding="utf-8"))
    target = capture.get("target")
    logs = capture.get("logs")
    if not isinstance(target, str) or not target.strip() or not isinstance(logs, list):
        raise ValueError("Capture must contain a target name and a logs array")
    return {"ota_tuples": extract_target_ota_tuples(logs, target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    print(json.dumps(reparse(args.capture), indent=2))


if __name__ == "__main__":
    main()
