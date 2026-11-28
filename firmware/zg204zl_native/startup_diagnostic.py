"""Bench-only startup isolation, applied after the normal pinned native overlay."""
import argparse
import hashlib
import json
from pathlib import Path

PROFILES = {
    'v0150-noidle': ('v0150', 0x01503001, 'skip_idle_sleep'),
    'v0151-suspend': ('v0151', 0x01513001, 'timer_sleep_uses_suspend'),
}
PM_SOURCE_SHA256 = '33a49aad1a0c71e947f6a339f04996f84ce6ec122001592f8dbf14000178ab38'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Startup diagnostic anchor changed')
    return text.replace(old, new, 1)


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
    if mode == 'skip_idle_sleep':
        output[main_path] = replace_once(app, '\t\t\tdrv_pm_lowPowerEnter();',
                            '\t\t\t/* Bench only: skip idle sleep, retain PM initialization. */').encode()
    else:
        name = 'SDK_z/proj/drivers/drv_pm.c'
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PM_SOURCE_SHA256:
            raise ValueError('PM source hash mismatch')
        output[name] = replace_once(raw.decode().replace('\r', ''),
            'drv_pm_sleep_mode_e sleepMode = (wakeupSrc & PM_WAKEUP_SRC_TIMER) ? '
            'PM_SLEEP_MODE_DEEP_WITH_RETENTION : PM_SLEEP_MODE_DEEPSLEEP;',
            'drv_pm_sleep_mode_e sleepMode = (wakeupSrc & PM_WAKEUP_SRC_TIMER) ? '
            'PM_SLEEP_MODE_SUSPEND : PM_SLEEP_MODE_DEEPSLEEP;').encode()
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
    print(json.dumps({'profile': args.profile, 'tag': tag, 'fileVersion': f'0x{version:08x}',
                      'mode': mode, 'benchOnly': True, 'outputs': {
                          n: hashlib.sha256(d).hexdigest() for n, d in output.items()}}, indent=2))
