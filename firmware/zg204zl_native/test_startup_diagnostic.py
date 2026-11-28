"""Hosted source controls for single-variable sleep diagnostics."""
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

import apply_native
import startup_diagnostic as diagnostic


@unittest.skipUnless(os.environ.get('ZG204ZL_SOURCE'), 'Hosted pristine upstream required')
class StartupDiagnostic(unittest.TestCase):
    def copy(self, root):
        source = Path(os.environ['ZG204ZL_SOURCE'])
        output = apply_native.sources(source)
        for name, data in output.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        startup = root / diagnostic.STARTUP_PATH
        startup.write_bytes((source / diagnostic.STARTUP_PATH).read_bytes())
        (root / diagnostic.MAIN_PATH).write_bytes((source / diagnostic.MAIN_PATH).read_bytes())
        with zipfile.ZipFile(source / 'tools/SDK_z.zip') as archive:
            path = root / 'SDK_z/proj/drivers/drv_pm.c'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read('proj/drivers/drv_pm.c'))

    def test_noidle_changes_only_the_idle_call_and_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.copy(root)
            result = diagnostic.sources(root, 'v0150-noidle')
            self.assertEqual(set(result), {'src/app_main.c', 'src/version_cfg.h'})
            original = (root / 'src/app_main.c').read_text()
            changed = result['src/app_main.c'].decode()
            self.assertEqual(changed.replace(
                '\t\t\t/* Bench only: skip idle sleep, retain PM initialization. */',
                '\t\t\tdrv_pm_lowPowerEnter();'), original)
            self.assertIn(b'0x50', result['src/version_cfg.h'])
            self.assertNotIn(b'PM_ENABLE 0', result['src/app_main.c'])

    def test_suspend_changes_only_timer_sleep_mode_and_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.copy(root)
            result = diagnostic.sources(root, 'v0151-suspend')
            name = 'SDK_z/proj/drivers/drv_pm.c'
            self.assertEqual(set(result), {name, 'src/version_cfg.h'})
            expected = (root / name).read_bytes().decode().replace('\r', '').replace(
                '? PM_SLEEP_MODE_DEEP_WITH_RETENTION : PM_SLEEP_MODE_DEEPSLEEP;',
                '? PM_SLEEP_MODE_SUSPEND : PM_SLEEP_MODE_DEEPSLEEP;', 1)
            self.assertEqual(result[name].decode(), expected)
            self.assertIn(b'0x51', result['src/version_cfg.h'])

    def test_bad_sdk_and_repeated_application_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.copy(root)
            path = root / 'SDK_z/proj/drivers/drv_pm.c'
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                diagnostic.sources(root, 'v0151-suspend')
            output = diagnostic.sources(root, 'v0150-noidle')
            (root / 'src/version_cfg.h').write_bytes(output['src/version_cfg.h'])
            with self.assertRaisesRegex(ValueError, 'anchor changed'):
                diagnostic.sources(root, 'v0150-noidle')

    def test_idle_suspend_covers_pad_wake_without_changing_other_sleep_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.copy(root)
            result = diagnostic.sources(root, 'v0153-idle-suspend')
            name = 'SDK_z/proj/drivers/drv_pm.c'
            self.assertEqual(set(result), {name, 'src/version_cfg.h'})
            baseline = diagnostic.sources(root, 'v0151-suspend')[name].decode()
            # The first matching conditional is for the other chip branch.
            before, separator, after = baseline.rpartition(
                '(wakeupSrc & PM_WAKEUP_SRC_TIMER) ? PM_SLEEP_MODE_SUSPEND : PM_SLEEP_MODE_DEEPSLEEP;')
            self.assertTrue(separator)
            self.assertEqual(result[name].decode(), before + 'PM_SLEEP_MODE_SUSPEND;' + after)
            self.assertIn(b'0x53', result['src/version_cfg.h'])

    def test_flash_wake_precedes_cache_invalidation_and_keeps_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.copy(root)
            name = diagnostic.STARTUP_PATH
            result = diagnostic.sources(root, 'v0152-flash-wake-first')
            self.assertEqual(set(result), {name, 'src/version_cfg.h'})
            self.assertIn(b'0x52', result['src/version_cfg.h'])
            old = (root / name).read_bytes().decode()
            new = result[name].decode()
            self.assertLess(new.index('FLASH_WAKEUP_BEGIN:'), new.index('FLASH_WAKEUP_SETTLE:'))
            self.assertLess(new.index('FLASH_WAKEUP_SETTLE:'), new.index('ZERO_TAG_BEGIN:'))
            self.assertLess(new.index('SET_IC:'), new.index('RANA_REG_BEGIN:'))
            self.assertEqual(new[new.index('RANA_REG_BEGIN:'):], old[old.index('RANA_REG_BEGIN:'):])
            self.assertIn('\ttstorerb    r1,[r0,#1]\n\ttmov\tr2,#128', new)
            with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                diagnostic.wake_flash_first(result[name])

    def test_boot_stages_preserve_noidle_and_cover_startup_and_first_application_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.copy(root)
            result = diagnostic.sources(root, 'v0154-boot-stages')
            self.assertEqual(set(result), {diagnostic.MAIN_PATH, 'src/app_main.c', 'src/version_cfg.h'})
            self.assertIn(b'0x54', result['src/version_cfg.h'])
            source = result[diagnostic.MAIN_PATH] + result['src/app_main.c']
            self.assertEqual(sorted(int(stage) for stage in re.findall(rb'zg_boot_stage = (\d+);', source)),
                             sorted(diagnostic.STAGES))
            self.assertNotIn(b'drv_pm_lowPowerEnter();', result['src/app_main.c'])
