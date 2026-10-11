"""Validate PM and non-PM BSEED Router candidates as one release contract.

This is an offline/source-only gate. It performs no live Zigbee operations.
"""
from __future__ import annotations

import json
from pathlib import Path

from bseed_nonpm_release import ROUTER as NONPM_ROUTER
from bseed_pm_release import ROUTER as PM_ROUTER

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "pm": {
        "board": "OUTLET_BSEED_PM_TS011F",
        "image_type": 43556,
        "script": "make_scripts/build_bseed_ts011f_pm_v8.sh",
        "release_helper": "helper_scripts/bseed_pm_release.py",
    },
    "nonpm": {
        "board": "OUTLET_BSEED_TS011F",
        "image_type": 43555,
        "script": "make_scripts/build_bseed_ts011f_nonpm_router.sh",
        "release_helper": "helper_scripts/bseed_nonpm_release.py",
    },
}


def _ascii_build(candidate: dict) -> None:
    build = candidate["build"]
    if not (isinstance(build, str) and build.isascii() and 1 <= len(build) <= 16):
        raise AssertionError(f"invalid Basic build ID: {build!r}")


def _function(source: str, start: str, end: str) -> str:
    try:
        return source.split(start, 1)[1].split(end, 1)[0]
    except IndexError as exc:
        raise AssertionError(f"missing source span: {start!r} -> {end!r}") from exc


def validate() -> dict:
    candidates = {"pm": dict(PM_ROUTER), "nonpm": dict(NONPM_ROUTER)}
    for family, candidate in candidates.items():
        expected = EXPECTED[family]
        _ascii_build(candidate)
        if candidate.get("role") != "Router":
            raise AssertionError(f"{family} candidate is not Router")
        if candidate.get("type") != expected["image_type"]:
            raise AssertionError(f"{family} Router image type drift")
        script = (ROOT / expected["script"]).read_text(encoding="utf-8")
        if "DEVICE_TYPE=router" not in script:
            raise AssertionError(f"{family} build no longer pins DEVICE_TYPE=router")
        if str(expected["image_type"]) not in script:
            raise AssertionError(f"{family} build no longer pins Router image type")
        helper_name = Path(expected["release_helper"]).name
        if helper_name not in script:
            raise AssertionError(f"{family} build no longer consumes canonical release helper")

    if candidates["pm"]["type"] == candidates["nonpm"]["type"]:
        raise AssertionError("PM/non-PM Router image types must remain distinct")
    if candidates["pm"]["build"] == candidates["nonpm"]["build"]:
        raise AssertionError("PM/non-PM Router Basic build IDs must remain board-specific")

    source = (ROOT / "src/telink/hal/zigbee_network.c").read_text(encoding="utf-8")
    rejoin = _function(
        source,
        "static bool start_rejoin_with_backoff(void) {",
        "static void notify_about_network_status_change",
    )
    steering = _function(
        source,
        "void hal_zigbee_start_network_steering(void) {",
        "hal_zigbee_status_t hal_zigbee_send_announce",
    )
    callback = _function(
        source,
        "void bdb_commissioning_callback(u8 status, void *arg) {",
        "void bdb_identify_callback",
    )
    init = _function(
        source,
        "void telink_zigbee_hal_network_init(void) {",
        "void telink_zigbee_hal_bdb_init",
    )

    board_guards = ("BSEED_PM_B28WRPVX", "BSEED_TS011F_NONPM")
    for name, body in (("rejoin", rejoin), ("steering", steering)):
        for guard in board_guards:
            if guard in body:
                raise AssertionError(f"shared Router {name} path became board-gated by {guard}")

    parent_lost = callback.split("case BDB_COMMISSION_STA_PARENT_LOST:", 1)[1].split(
        "case BDB_COMMISSION_STA_REJOIN_FAILURE:", 1
    )[0]
    rejoin_failure = callback.split("case BDB_COMMISSION_STA_REJOIN_FAILURE:", 1)[1].split(
        "default:", 1
    )[0]
    for block_name, body in (("parent-loss", parent_lost), ("rejoin-failure", rejoin_failure)):
        if "start_rejoin_with_backoff" not in body:
            raise AssertionError(f"{block_name} no longer uses shared rejoin recovery")
        for guard in board_guards:
            if guard in body:
                raise AssertionError(f"{block_name} recovery became board-gated by {guard}")

    if "#if ZB_ROUTER_ROLE" not in init:
        raise AssertionError("Router parent/receiver invariants are no longer role-gated")
    if "g_zbNIB.parentInfo = MAC_DATA_POLL_KEEPALIVE_BIT" not in init:
        raise AssertionError("Router sleepy-child keepalive capability drifted")

    result = {
        "schema": 1,
        "families": {
            family: {
                "build": candidate["build"],
                "fileVersion": candidate["version"],
                "imageType": candidate["type"],
                "board": EXPECTED[family]["board"],
                "buildScript": EXPECTED[family]["script"],
            }
            for family, candidate in candidates.items()
        },
        "sharedRecoveryBoardNeutral": True,
        "routerKeepaliveRoleGated": True,
    }
    return result


def main() -> None:
    print(json.dumps(validate(), indent=2))


if __name__ == "__main__":
    main()
