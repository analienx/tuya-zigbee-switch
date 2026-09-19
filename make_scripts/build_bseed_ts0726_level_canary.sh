#!/usr/bin/env bash
set -euo pipefail
# Build only: never publish, flash, change a live device, or update an OTA index.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -le 1 ]] || { echo 'usage: build_bseed_ts0726_level_canary.sh [output-dir]' >&2; exit 2; }
export BSEED_TS0726_RELEASE_CHANNEL=currentlevel-canary
exec bash "$ROOT/make_scripts/build_bseed_ts0726_v8.sh" "${1:-build/bseed-ts0726-level-canary}"
