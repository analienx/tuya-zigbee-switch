#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

BSEED_MANUFACTURERS = {
    "_TZ3000_b28wrpvx",
    "b28wrpvx",
    "_TZ3000_o1jzcxou",
    "o1jzcxou",
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
names = [entry.get("manufacturerName") or [] for entry in bseed]
if any(len(name_list) != 1 for name_list in names):
    raise SystemExit("dedicated BSEED entries must each have one exact manufacturerName")
actual_names = {name_list[0] for name_list in names}
if actual_names != BSEED_MANUFACTURERS or len(bseed) != len(BSEED_MANUFACTURERS):
    raise SystemExit(f"dedicated BSEED index has wrong exact manufacturer set: {sorted(actual_names)}")

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
