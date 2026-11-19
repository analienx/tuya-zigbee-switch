#!/usr/bin/env python3
"""Fail-closed source overlay for the ZG-204ZL reliability canary, v0.1.4.1.

Apply ONLY to pvvx/ZigbeeTLc@c249d8a7caddb5012d06aba580d4a53d1957f9c1.
No proprietary stock bytes are used. No hardware access is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

UPSTREAM_COMMIT = "c249d8a7caddb5012d06aba580d4a53d1957f9c1"
NEXT_FILE_VERSION = 0x01413001

# These are Git blob IDs of the six unchanged upstream source files.
BASE_BLOBS = {
    "src/zb_appCb.c": "4d7db2b8563b91d6b3d56907c3277965f98ecf8a",
    "src/sensor_pir.c": "f92b4b71ee95d690775b433800ce6dbd123c2e17",
    "src/app_main.h": "c8e8bce5542d0fc66f7f855af2ab3fcc1aec7b46",
    "src/includes/zb_config.h": "8dbd01650f0ce2b8919d28d211ba0ca5365c2cc7",
    "src/app_cfg.h": "d0e9e446da23bbfb1fe99a7729134dc8ace806c6",
    "src/version_cfg.h": "99f849e5b1e6da41f125712b7901d64e70322057",
}


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def replace_once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{where}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def modified_sources(source: Path) -> dict[str, bytes]:
    """Read and verify ALL originals before returning any changed bytes."""
    originals: dict[str, str] = {}
    for name, expected in BASE_BLOBS.items():
        raw = (source / name).read_bytes()
        actual = git_blob_sha(raw)
        if actual != expected:
            raise ValueError(f"{name}: upstream blob mismatch {actual}; expected {expected}")
        originals[name] = raw.decode("utf-8")
    edits = dict(originals)

    # Keep the same Zigbee model/manufacturer/image type. Bump only APP_BUILD,
    # yielding 0x01413001, above upstream's 0x01403001.
    name = "src/version_cfg.h"
    edits[name] = replace_once(edits[name], "#define APP_BUILD\t\t\t\t0x40", "#define APP_BUILD\t\t\t\t0x41", name)

    # The ZG-204ZL runs on CR2450. Its upstream TX setting is +2.61 dBm;
    # use the next already-supported +3.01 dBm level. Do not claim RX power
    # changes: MAC RX already uses maximum gain outside active channel scans.
    name = "src/app_cfg.h"
    edits[name] = replace_once(
        edits[name],
        "#define ZB_TX_POWER_IDX_DEF\t\t\t\tRF_POWER_INDEX_P2p61dBm",
        "#define ZB_TX_POWER_IDX_DEF\t\t\t\tRF_POWER_INDEX_P3p01dBm",
        name,
    )

    # The cloned source is built ONLY for BOARD_ZG204ZL, so this stack config
    # change cannot be accidentally deployed to the other pvvx boards.
    name = "src/includes/zb_config.h"
    edits[name] = replace_once(
        edits[name],
        "#define\tZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t1",
        "#define\tZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t3",
        name,
    )

    name = "src/zb_appCb.c"
    edits[name] = replace_once(
        edits[name],
        """/* Rejoin period, ms. The full channel mask is scanned on every 4th attempt. */
static const u32 rejoinBackoffTbl[] = {
\t30*1000, 60*1000, 2*60*1000, 5*60*1000, 10*60*1000, 20*60*1000, 30*60*1000
};""",
        """/* ZG204ZL reliability canary: prompt but battery-bounded rejoin recovery.
 * The full channel mask is still scanned every fourth attempt; never lock
 * the device to the old parent, even when trying the known network channel.
 * Maximum idle retry interval is 10 min (down from 30 min). */
static const u32 rejoinBackoffTbl[] = {
\t5*1000, 10*1000, 20*1000, 45*1000, 90*1000,
\t3*60*1000, 5*60*1000, 10*60*1000
};""",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        "static u8 rj_step;\nstatic u8 rj_chan;",
        """static u8 rj_step;
static u16 zg204zl_rejoin_requests;
static u16 zg204zl_parent_lost_or_scan_failures;
static u32 zg204zl_motion_rejoin_tick;
static bool zg204zl_motion_rejoin_seen;
static u8 rj_chan;""",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        "    zb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration);\n\n    if(++rj_step == 0)",
        """    if (zg204zl_rejoin_requests != 0xffff)
        zg204zl_rejoin_requests++;
    sws_printf("ZG204ZL rejoin #%d step=%d mask=%x loss=%d\\n",
               zg204zl_rejoin_requests, rj_step, app_rejoinChannelMask(),
               zg204zl_parent_lost_or_scan_failures);
    zb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration);

    if(++rj_step == 0)""",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        "app_rejoinSchedule(100);\n}\n\n#if !USE_BLE\nstatic ev_timer_event_t *timerDevAnnceEvt;",
        """app_rejoinSchedule(100);
}

/* Motion gives a disconnected PIR a chance to reconnect promptly, even when
 * the scheduled retry has reached a long battery-saving interval. Prevent
 * continuous motion from defeating backoff: at most one prompt per 60 sec.
 * Existing button-initiated app_rejoinNow() remains unchanged. */
void app_rejoinOnMotion(void) {
    if (!g_sensorAppCtx.linkDown || zb_isDeviceFactoryNew())
        return;
    if (!zg204zl_motion_rejoin_seen ||
        clock_time_exceed(zg204zl_motion_rejoin_tick, 60UL * 1000UL * 1000UL)) {
        zg204zl_motion_rejoin_seen = TRUE;
        zg204zl_motion_rejoin_tick = clock_time();
        sws_puts("ZG204ZL: motion-triggered rejoin\\n");
        app_rejoinNow();
    }
}

#if !USE_BLE
static ev_timer_event_t *timerDevAnnceEvt;""",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        "\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tapp_saveNwkChannel();",
        "\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tzg204zl_motion_rejoin_seen = FALSE;\n\t\t\tapp_saveNwkChannel();",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        "\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\trj_step = 0;",
        "\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tzg204zl_motion_rejoin_seen = FALSE;\n\t\t\trj_step = 0;",
        name,
    )
    edits[name] = replace_once(
        edits[name],
        '\t\t\tsws_puts("BDB: no scan response or parent lost\\n");',
        """\t\t\tsws_puts("BDB: no scan response or parent lost\\n");
            if (zg204zl_parent_lost_or_scan_failures != 0xffff)
                zg204zl_parent_lost_or_scan_failures++;""",
        name,
    )

    name = "src/app_main.h"
    edits[name] = replace_once(
        edits[name], "void app_rejoinNow(void);\n",
        "void app_rejoinNow(void);\nvoid app_rejoinOnMotion(void);\n", name,
    )

    name = "src/sensor_pir.c"
    edits[name] = replace_once(
        edits[name],
        "\t\t\t\t// event On\n#if USE_SENSOR_RC2417",
        """\t\t\t\t// event On
                /* Only a newly rising PIR event can request an early retry.
                 * A 60 s cooldown in app_rejoinOnMotion limits battery use. */
                app_rejoinOnMotion();
#if USE_SENSOR_RC2417""",
        name,
    )
    return {name: data.encode("utf-8") for name, data in edits.items()}


def apply(source: Path, *, dry_run: bool = False, check_commit: bool = True) -> dict:
    if check_commit:
        head = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != UPSTREAM_COMMIT:
            raise ValueError(f"wrong ZigbeeTLc source commit {head}; expected {UPSTREAM_COMMIT}")
    changed = modified_sources(source)
    if not dry_run:
        # No file may be written until ALL blob and content checks have passed.
        for name, contents in changed.items():
            (source / name).write_bytes(contents)
    return {
        "status": "SOURCE_OVERLAY_VALIDATED",
        "base": UPSTREAM_COMMIT,
        "target_board": "BOARD_ZG204ZL",
        "file_version": f"0x{NEXT_FILE_VERSION:08x}",
        "tx_power_old_dbm": 2.61,
        "tx_power_new_dbm": 3.01,
        "receive_gain": "unchanged (maximum gain already used for normal reception)",
        "scan_attempts": 3,
        "rejoin_delays_s": [5, 10, 20, 45, 90, 180, 300, 600],
        "motion_rejoin_min_interval_s": 60,
        "output_blobs": {name: git_blob_sha(data) for name, data in changed.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(args.source.resolve(), dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()
