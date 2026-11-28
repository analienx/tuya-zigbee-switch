"""Hosted source controls for single-variable sleep diagnostics."""
import os
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
