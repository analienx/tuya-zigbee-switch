"""Hosted source-integrity tests; no live device or local executor."""
import os
import tempfile
import unittest
from pathlib import Path

import apply_native as overlay


@unittest.skipUnless(os.environ.get('ZG204ZL_SOURCE'), 'Upstream present in hosted source job')
class Overlay(unittest.TestCase):
    def copy(self, root):
        source = Path(os.environ['ZG204ZL_SOURCE'])
        for name in overlay.baseline.BASE_BLOBS | overlay.EXTRA_PINS:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())

    def test_exact_pins_and_all_new_blobs_fail_closed_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.copy(root)
            originals = {n: (root / n).read_bytes() for n in overlay.EXTRA_PINS}
            output = overlay.sources(root)
            self.assertIn('src/nc_all.c', output)
            self.assertIn(b'0x49', output['src/version_cfg.h'])
            for name, original in originals.items():
                with self.subTest(name=name):
                    (root / name).write_bytes(original + b'\n/* changed */\n')
                    with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                        overlay.sources(root)
                    (root / name).write_bytes(original)
            self.assertEqual({n: (root / n).read_bytes() for n in originals}, originals)

    def test_overlay_preserves_nv_version_and_recovery_and_replaces_all_senders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.copy(root)
            output = overlay.sources(root)
            old_cfg = (root / 'src/app_cfg.h').read_text()
            cfg = output['src/app_cfg.h'].decode()
            self.assertEqual([l for l in old_cfg.splitlines() if '#define USE_NV_APP' in l],
                             [l for l in cfg.splitlines() if '#define USE_NV_APP' in l])
            for name in ('src/sensor_pir.c', 'src/zcl_appCb.c', 'src/app_onoff.h'):
                self.assertNotIn(b'remoteCmdOnOff(', output[name])
                self.assertNotIn(b'taskRetryOnOff(', output[name])
            pir = output['src/sensor_pir.c'].decode()
            self.assertLess(pir.index('app_rejoinOnMotion();'), pir.index('nc_motion();'))
            self.assertIn(b'nc_rx, nc_aps_confirm', output['src/app_main.c'])
            self.assertIn(b'nc_cluster_capacity', output['src/app_EpCfg.c'])
