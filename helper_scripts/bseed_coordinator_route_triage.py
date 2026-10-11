#!/usr/bin/env python3
"""Read-only coordinator source-route triage for Zigbee2MQTT.

Accept an operator-supplied LOCAL log extract, output only bounded counters
and redacted diagnostic recommendations. No MQTT publishes, maps, adapter
resets, permit-join, coordinator writes, credentials or network keys.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import re
from pathlib import Path

OUTBOUND_ERRORS = {
    "NWK_NO_ROUTE": re.compile(r"NWK_NO_ROUTE", re.I),
    "SOURCE_ROUTE_FAILURE": re.compile(r"SOURCE_ROUTE_FAILURE|ROUTE_ERROR_SOURCE_ROUTE", re.I),
    "MANY_TO_ONE_ROUTE_FAILURE": re.compile(r"MANY_TO_ONE_ROUTE_FAILURE|ROUTE_ERROR_MANY_TO_ONE", re.I),
    "MAC_NO_ACK": re.compile(r"MAC_NO_ACK|MAC_CHANNEL_ACCESS_FAILURE", re.I),
    "DELIVERY_FAILED": re.compile(r"ZIGBEE_DELIVERY_FAILED|DELIVERY_FAILED|read.*timed out|ZCL.*timeout", re.I),
}
INBOUND_PATTERNS = (
    re.compile(r"Received Zigbee message from", re.I),
    re.compile(r"device_announce|deviceannounce", re.I),
    re.compile(r"ZCL.*(response|report)\b", re.I),
)
TARGET_IEEE = re.compile(r"^0x[0-9a-f]{16}$", re.I)


def diagnose(lines: list[str], *, ieee: str | None, nwk: int | None,
             adapter: str) -> dict:
    tokens = []
    if ieee:
        if not TARGET_IEEE.fullmatch(ieee):
            raise ValueError("IEEE must be an exact 0x-prefixed 16-digit address")
        tokens.append(re.compile(r"(?<![0-9a-f])" + re.escape(ieee) + r"(?![0-9a-f])", re.I))
    if nwk is not None:
        if not 0 <= nwk <= 0xFFFF:
            raise ValueError("NWK must be a 16-bit address")
        # NWK can appear as hex or decimal, but do not confuse unrelated IDs.
        values = [str(nwk), f"0x{nwk:04x}", f"0x{nwk:x}"]
        tokens.extend(re.compile(r"(?<![0-9a-f])" + re.escape(v) + r"(?![0-9a-f])", re.I)
                      for v in values)
    if not tokens:
        raise ValueError("Target IEEE and/or NWK is required")
    counts: Counter[str] = Counter()
    examined = 0
    matched = 0
    for line in lines:
        examined += 1
        if not any(pattern.search(line) for pattern in tokens):
            continue
        matched += 1
        for name, pattern in OUTBOUND_ERRORS.items():
            if pattern.search(line):
                counts[name] += 1
        if any(pattern.search(line) for pattern in INBOUND_PATTERNS):
            counts["INBOUND_EVIDENCE"] += 1
    outbound = sum(counts[name] for name in OUTBOUND_ERRORS)
    inbound = counts["INBOUND_EVIDENCE"]
    if outbound and inbound:
        classification = "POSSIBLE_ASYMMETRIC_COORDINATOR_ROUTE"
    elif outbound:
        classification = "COORDINATOR_OUTBOUND_FAILURE_NO_INBOUND_PROOF"
    elif inbound:
        classification = "INBOUND_SEEN_NO_OUTBOUND_FAILURE"
    else:
        classification = "INSUFFICIENT_TARGET_EVIDENCE"
    recommendations = [
        "Check live IEEE-to-NWK mapping before targeting a device; NWK IDs can change.",
        "Compare coordinator-originated ZCL read success with device-originated ZDO/ZCL reports over the SAME observation window.",
        "Check coordinator route discovery and route record counters; an inbound announce does not prove reverse-path usability.",
        "Do not rejoin/reset an otherwise healthy Router based solely on coordinator NWK_NO_ROUTE.",
    ]
    if adapter == "ember":
        recommendations.append(
            "Inspect the actual Zigbee2MQTT Ember stack_config.json concentrator "
            "settings and source-route table pressure; verify hardware adapter and "
            "firmware before proposing a targeted MTORR refresh."
        )
    elif adapter == "zstack":
        recommendations.append(
            "This is Z-Stack: do NOT apply Ember stack_config.json or MTORR SDK APIs. "
            "Investigate Z-Stack route discovery/source-route management and "
            "coordinator firmware separately before any write or restart."
        )
    else:
        recommendations.append(
            "Confirm adapter family (zstack vs ember) before any routing remediation."
        )
    return {
        "schema": 1, "adapter": adapter, "lines_examined": examined,
        "target_matched_lines": matched, "counts": dict(sorted(counts.items())),
        "classification": classification, "recommendations": recommendations,
        "mutations_performed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ieee", help="Full device IEEE starting with 0x")
    parser.add_argument("--nwk", type=lambda x: int(x, 0), help="16-bit NWK number (0xHEX or decimal)")
    parser.add_argument("--adapter", choices=("zstack", "ember", "unknown"),
                        default="unknown")
    args = parser.parse_args()
    if args.ieee is None and args.nwk is None:
        parser.error("One of --ieee or --nwk is required")
    try:
        result = diagnose(args.log.read_text(encoding="utf-8", errors="replace").splitlines(),
                          ieee=args.ieee, nwk=args.nwk, adapter=args.adapter)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
