"""Bench-only startup isolation, applied after the normal pinned native overlay."""
import argparse
import hashlib
import json
from pathlib import Path

from apply_native import baseline

PROFILES = {
    'v0150-noidle': ('v0150', 0x01503001, 'skip_idle_sleep'),
    'v0151-suspend': ('v0151', 0x01513001, 'timer_sleep_uses_suspend'),
    'v0152-flash-wake-first': ('v0152', 0x01523001, 'wake_flash_before_cache_reset'),
    'v0153-idle-suspend': ('v0153', 0x01533001, 'all_idle_sleep_uses_suspend'),
    'v0154-boot-stages': ('v0154', 0x01543001, 'mark_startup_and_skip_idle_sleep'),
}
PM_SOURCE_SHA256 = '33a49aad1a0c71e947f6a339f04996f84ce6ec122001592f8dbf14000178ab38'
STARTUP_BLOB = 'f2b62391e1e6247ecdd64d34e86e1fd1808cc898'
STARTUP_PATH = 'src/patch_z_sdk/cstartup_8258.S'
MAIN_PATH = 'src/main.c'
MAIN_BLOB = '140e9087fa917c2ab6bb42207047bc0f37d5eec8'
STAGES = {
    1: 'before platform initialization', 2: 'after OS initialization',
    3: 'before native initialization', 4: 'after native initialization',
    5: 'after initial sensor reading', 6: 'after user initialization',
    7: 'before event processing', 8: 'before Zigbee stack processing',
    9: 'after Zigbee stack processing', 10: 'at app task entry',
    11: 'after key processing', 12: 'before native service',
    13: 'after native service', 14: 'after rejoin service',
}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Startup diagnostic anchor changed')
    return text.replace(old, new, 1)


def wake_flash_first(raw):
    if baseline.git_blob_sha(raw) != STARTUP_BLOB:
        raise ValueError('Startup assembly blob mismatch')
    source = raw.decode()
    border = '@' + '*' * 104 + '\n'
    cache_start = source.index(border + '@                                    IC TAG INITIALIZATION\n')
    flash_start = source.index(border + '@                                    DCDC INITIALIZATION FOR FLASH\n')
    wake_tail = 'FLASH_WAKEUP_END:\n#endif\n'
    flash_end = source.index(wake_tail) + len(wake_tail)
    cache = source[cache_start:flash_start]
    flash = source[flash_start:flash_end]
    # Wait after CS rises, when release from flash power-down starts.
    # 1024 iterations of three instructions take at least 64 us at 48 MHz.
    flash = replace_once(flash, wake_tail,
        '\ttmov\tr2,#128\n'
        '\ttshftl\tr2,r2,#3\n'
        'FLASH_WAKEUP_SETTLE:\n'
        '\ttsub\tr2,#1\n'
        '\ttcmp\tr2,#0\n'
        '\ttjne\tFLASH_WAKEUP_SETTLE\n' + wake_tail)
    return (source[:cache_start] + flash + cache + source[flash_end:]).encode()


def mark_startup(raw_main, app):
    if baseline.git_blob_sha(raw_main) != MAIN_BLOB:
        raise ValueError('Main source blob mismatch')
    main = raw_main.decode()
    main = replace_once(main, '#include "tl_common.h"\n',
                        '#include "tl_common.h"\nvolatile u8 zg_boot_stage;\n')
    main = replace_once(main, 'int flash_main(void)\n{\n',
                        'int flash_main(void)\n{\n\tzg_boot_stage = 1;\n')
    for call, stage in [('os_init(isRetention);', 2), ('tl_zbTaskProcedure();', 9)]:
        main = replace_once(main, call, f'{call}\n\tzg_boot_stage = {stage};')
    main = replace_once(main, '#else // USE_BLE\n\tuser_init(isRetention);',
                        '#else // USE_BLE\n\tuser_init(isRetention);\n\tzg_boot_stage = 6;')
    main = replace_once(main, 'ev_main();', 'zg_boot_stage = 7;\n\t\tev_main();\n\t\tzg_boot_stage = 8;')
    app = replace_once(app, '#include "nc_port_tlsr.h"\n',
                       '#include "nc_port_tlsr.h"\nextern volatile u8 zg_boot_stage;\n')
    app = replace_once(app, '\tnc_init();', '\tzg_boot_stage = 3;\n\tnc_init();\n\tzg_boot_stage = 4;')
    app = replace_once(app, '    // read sensors\n\tread_sensor_and_show();',
                       '    // read sensors\n\tread_sensor_and_show();\n\tzg_boot_stage = 5;')
    app = replace_once(app, 'void app_task(void)\n{\n\tu16 rep_uptime_sec;',
                       'void app_task(void)\n{\n\tu16 rep_uptime_sec;\n\tzg_boot_stage = 10;')
    app = replace_once(app, '\tsensors_task(NULL);\n\ttask_keys();',
                       '\tsensors_task(NULL);\n\ttask_keys();\n\tzg_boot_stage = 11;')
    app = replace_once(app, '\tnc_service();\n\tapp_rejoinService();',
                       '\tzg_boot_stage = 12;\n\tnc_service();\n\tzg_boot_stage = 13;\n'
                       '\tapp_rejoinService();\n\tzg_boot_stage = 14;')
    return main.encode(), app.encode()


def sources(root, profile):
    tag, version, mode = PROFILES[profile]
    version_path = 'src/version_cfg.h'
    main_path = 'src/app_main.c'
    app = (root / main_path).read_text()
    if app.count('nc_init();') != 1 or app.count('nc_service();') != 1:
        raise ValueError('Normal native-control overlay is required first')
    output = {version_path: replace_once((root / version_path).read_text(),
              '#define APP_BUILD\t\t\t\t0x49',
              f'#define APP_BUILD\t\t\t\t0x{(version >> 16) & 255:02x}').encode()}
    if mode in ('skip_idle_sleep', 'mark_startup_and_skip_idle_sleep'):
        output[main_path] = replace_once(app, '\t\t\tdrv_pm_lowPowerEnter();',
                            '\t\t\t/* Bench only: skip idle sleep, retain PM initialization. */').encode()
        if mode == 'mark_startup_and_skip_idle_sleep':
            output[MAIN_PATH], output[main_path] = mark_startup(
                (root / MAIN_PATH).read_bytes(), output[main_path].decode())
    elif mode in ('timer_sleep_uses_suspend', 'all_idle_sleep_uses_suspend'):
        name = 'SDK_z/proj/drivers/drv_pm.c'
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PM_SOURCE_SHA256:
            raise ValueError('PM source hash mismatch')
        sleep_mode = 'PM_SLEEP_MODE_SUSPEND'
        if mode == 'timer_sleep_uses_suspend':
            sleep_mode = '(wakeupSrc & PM_WAKEUP_SRC_TIMER) ? PM_SLEEP_MODE_SUSPEND : PM_SLEEP_MODE_DEEPSLEEP'
        output[name] = replace_once(
            raw.decode().replace('\r', ''),
            'drv_pm_sleep_mode_e sleepMode = (wakeupSrc & PM_WAKEUP_SRC_TIMER) ? '
            'PM_SLEEP_MODE_DEEP_WITH_RETENTION : PM_SLEEP_MODE_DEEPSLEEP;',
            f'drv_pm_sleep_mode_e sleepMode = {sleep_mode};',
        ).encode()
    else:
        output[STARTUP_PATH] = wake_flash_first((root / STARTUP_PATH).read_bytes())
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--profile', choices=PROFILES, required=True)
    args = p.parse_args()
    output = sources(args.source, args.profile)
    for name, data in output.items():
        (args.source / name).write_bytes(data)
    tag, version, mode = PROFILES[args.profile]
    receipt = {'profile': args.profile, 'tag': tag, 'fileVersion': f'0x{version:08x}',
               'mode': mode, 'benchOnly': True, 'outputs': {
                   n: hashlib.sha256(d).hexdigest() for n, d in output.items()}}
    if mode == 'mark_startup_and_skip_idle_sleep':
        receipt['bootStages'] = STAGES
    print(json.dumps(receipt, indent=2))
