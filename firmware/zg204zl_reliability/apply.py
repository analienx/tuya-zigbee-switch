#!/usr/bin/env python3
"""Fail-closed ZG-204ZL overlay for one immutable upstream commit."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import NamedTuple

UPSTREAM_COMMIT = 'c249d8a7caddb5012d06aba580d4a53d1957f9c1'


class Variant(NamedTuple):
    file_version: int
    tx_index: str
    tx_dbm: float


VARIANTS = {
    'v0146': Variant(0x01463001, 'RF_POWER_INDEX_P2p61dBm', 2.61),
    'v0147': Variant(0x01473001, 'RF_POWER_INDEX_P3p01dBm', 3.01),
}

BASE_BLOBS = {
    'src/zb_appCb.c': '4d7db2b8563b91d6b3d56907c3277965f98ecf8a',
    'src/sensor_pir.c': 'f92b4b71ee95d690775b433800ce6dbd123c2e17',
    'src/app_main.c': '12305c8805ac6879cf94301d9418db2741009724',
    'src/app_main.h': 'c8e8bce5542d0fc66f7f855af2ab3fcc1aec7b46',
    'src/includes/zb_config.h': '8dbd01650f0ce2b8919d28d211ba0ca5365c2cc7',
    'src/app_cfg.h': 'd0e9e446da23bbfb1fe99a7729134dc8ace806c6',
    'src/version_cfg.h': '99f849e5b1e6da41f125712b7901d64e70322057',
}


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def replace_once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f'{where}: expected exactly one anchor, found {count}')
    return text.replace(old, new, 1)


def modified_sources(source: Path, variant: str) -> dict[str, bytes]:
    spec = VARIANTS[variant]
    edits = {}
    for name, expected in BASE_BLOBS.items():
        raw = (source / name).read_bytes()
        if git_blob_sha(raw) != expected:
            raise ValueError(f'{name}: upstream blob mismatch; expected {expected}')
        edits[name] = raw.decode('utf-8')
    name = 'src/version_cfg.h'
    build = (spec.file_version >> 16) & 0xff
    edits[name] = replace_once(edits[name], '#define APP_BUILD\t\t\t\t0x40',
                               f'#define APP_BUILD\t\t\t\t0x{build:02x}', name)
    name = 'src/app_cfg.h'
    edits[name] = replace_once(edits[name],
        '#define ZB_TX_POWER_IDX_DEF\t\t\t\tRF_POWER_INDEX_P2p61dBm',
        f'#define ZB_TX_POWER_IDX_DEF\t\t\t\t{spec.tx_index}', name)
    name = 'src/includes/zb_config.h'
    edits[name] = replace_once(edits[name],
        '#define\tZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t1',
        '#define\tZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t3', name)
    name = 'src/zb_appCb.c'
    start = '/* Rejoin period, ms. The full channel mask is scanned on every 4th attempt. */'
    end = '#if !USE_BLE\nstatic ev_timer_event_t *timerDevAnnceEvt;'
    if edits[name].count(start) != 1 or edits[name].count(end) != 1:
        raise ValueError('recovery section anchors changed')
    begin, finish = edits[name].index(start), edits[name].index(end)
    if finish <= begin:
        raise ValueError('recovery section order changed')
    recovery = Path(__file__).with_name('recovery.inc').read_text(encoding='utf-8')
    edits[name] = edits[name][:begin] + recovery + '\n\n' + edits[name][finish:]
    # Cancel the owned cooldown on both restored-network initialization and
    # completed commissioning. The latter already cancels the retry timer.
    edits[name] = replace_once(edits[name],
        '\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tapp_saveNwkChannel();',
        '\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tapp_rejoinReset();\n\t\t\tapp_saveNwkChannel();', name)
    edits[name] = replace_once(edits[name],
        '\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\trj_step = 0;',
        '\t\t\tg_sensorAppCtx.linkDown = 0;\n\t\t\tapp_rejoinReset();\n\t\t\trj_step = 0;', name)
    edits[name] = replace_once(edits[name],
        '\t\t\t\tzb_rejoinSecModeSet(REJOIN_SECURITY);\n#endif\n\t\t\t\tzb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration);\n\t\t\t\tapp_rejoinSchedule(rejoinBackoffTbl[0]);',
        '\t\t\t\trj_secMode = REJOIN_SECURITY;\n#endif\n\t\t\t\tapp_rejoinSchedule(100);', name)
    # This upstream entry point requests recovery before BDB has necessarily
    # delivered PARENT_LOST. Keep it eligible for the linkDown-guarded callback.
    edits[name] = replace_once(edits[name],
        '        rj_secMode = REJOIN_INSECURITY;\n        app_rejoinSchedule(1000);',
        '        g_sensorAppCtx.linkDown = 1;\n        rj_secMode = REJOIN_INSECURITY;\n        app_rejoinSchedule(1000);', name)
    name = 'src/app_main.h'
    edits[name] = replace_once(edits[name], 'void app_rejoinNow(void);\n',
        'void app_rejoinNow(void);\nvoid app_rejoinOnMotion(void);\nvoid app_rejoinService(void);\n', name)
    name = 'src/sensor_pir.c'
    edits[name] = replace_once(edits[name], '\t\t\t\t// event On\n#if USE_SENSOR_RC2417',
        '\t\t\t\t// event On\n\t\t\t\tapp_rejoinOnMotion();\n#if USE_SENSOR_RC2417', name)
    name = 'src/app_main.c'
    edits[name] = replace_once(edits[name],
        '\tif(bdb_isIdle()) {\n\t\t// report handler',
        '\tapp_rejoinService();\n\tif(bdb_isIdle()) {\n\t\t// report handler', name)
    # This overlay only builds the displayless ZG-204ZL. Remove its periodic
    # unjoined heartbeat. Motion and pairing feedback use other call sites.
    edits[name] = replace_once(edits[name],
        '\t\t} else { // Device not Joined\n'
        '#if\tUSE_DISPLAY\n'
        '\t\t\tif(!g_zcl_thermostatUICfgAttrs.display_off) {\n'
        '\t\t\t\tif(!g_sensorAppCtx.timerLedEvt) {\n'
        '\t\t\t\t\tshow_connected_symbol(false);\n'
        '\t\t\t\t\tupdate_lcd();\n'
        '\t\t\t\t}\n'
        '\t\t\t}\n'
        '#ifdef USE_BLINK_LED\n'
        '\t\t\tif(!g_sensorAppCtx.timerLedEvt)\n'
        '\t\t\t\tlight_blink_start(1, 50, 10000);\n'
        '#endif // USE_BLINK_LED\n'
        '#else // !USE_DISPLAY\n'
        '\t\t\tif(!g_sensorAppCtx.timerLedEvt)\n'
        '\t\t\t\tlight_blink_start(1, 50, 10000);\n'
        '\t\t\t//gpio_write(GPIO_LED, LED_ON); // - не включать PIN_PULLUP/PULLDOWN !\n'
        '#endif // USE_DISPLAY\n'
        '\t\t}\n',
        '\t\t}\n', name)
    return {name: text.encode('utf-8') for name, text in edits.items()}


def apply(source: Path, variant: str, *, dry_run: bool = False, check_commit: bool = True) -> dict:
    spec = VARIANTS[variant]
    if check_commit:
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != UPSTREAM_COMMIT:
            raise ValueError(f'wrong ZigbeeTLc source commit {head}; expected {UPSTREAM_COMMIT}')
    changed = modified_sources(source, variant)
    if not dry_run:
        for name, contents in changed.items():
            (source / name).write_bytes(contents)
    return {
        'status': 'SOURCE_OVERLAY_VALIDATED', 'base': UPSTREAM_COMMIT,
        'target_board': 'BOARD_ZG204ZL', 'variant': variant,
        'file_version': f'0x{spec.file_version:08x}',
        'tx_power_old_dbm': 2.61, 'tx_power_new_dbm_nominal': spec.tx_dbm,
        'tx_supply_mode': 'VANT', 'tx_power_index': spec.tx_index,
        'receive_gain': 'unchanged (maximum normal RX gain)',
        'scan_attempts': 3, 'rejoin_delays_s': [2, 3, 5, 10, 20, 40, 80, 300, 900, 1800],
        'sdk_deferral_delays_s': [1, 1, 1, 5, 15, 60, 300],
        'retry_timer_allocation': 'retry on normal app passes after an allocation failure',
        'motion_rejoin_min_interval_s': 300,
        'motion_policy': 'accelerate one retry after 300 s since any accepted request; preserve progression; coalesce while busy',
        'channel_policy': 'known, known, full, full; both alternating security modes cover both masks',
        'led_policy': 'periodic unjoined blink removed; retain motion and commissioning feedback',
        'diagnostics': 'printf disabled in the production candidate; no runtime counters claimed',
        'output_blobs': {name: git_blob_sha(data) for name, data in changed.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--variant', choices=VARIANTS, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.source.resolve(), args.variant, dry_run=args.dry_run), indent=2))


if __name__ == '__main__':
    main()
