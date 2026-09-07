#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

BSEED_MANUFACTURERS = {
    "_TZ3000_b28wrpvx",
    "b28wrpvx",
    "_TZ3002_iedhxgyi",
    "iedhxgyi",
}


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_bseed(entry: dict) -> bool:
    names = entry.get("manufacturerName") or []
    return bool(BSEED_MANUFACTURERS.intersection(names))


def write(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


parser = argparse.ArgumentParser(
    description="Replace inherited BSEED entries with the dedicated current index"
)
parser.add_argument("--bseed-index", type=Path, required=True)
parser.add_argument("--router-index", type=Path, required=True)
parser.add_argument("--force-index", type=Path, required=True)
args = parser.parse_args()

bseed = load(args.bseed_index)
if len(bseed) != 4 or any(not is_bseed(entry) for entry in bseed):
    raise SystemExit("dedicated BSEED index must contain exactly four BSEED entries")

router = load(args.router_index)
router_without_stale = [entry for entry in router if not is_bseed(entry)]
write(args.router_index, router_without_stale + bseed)

# Do not leave inherited forced BSEED images available. The dedicated public
# index intentionally exposes only normal current updates and stock conversion
# wrappers; recovery/forced images remain explicit supervised artifacts.
force = load(args.force_index)
write(args.force_index, [entry for entry in force if not is_bseed(entry)])

print(
    "router BSEED entries replaced with current dedicated index; "
    "stale BSEED FORCE entries removed"
)
