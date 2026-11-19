"""GitHub-hosted regression tests for the ZG-204ZL source overlay.

Requires the immutable upstream checkout in ZG204ZL_SOURCE. Never opens SWire,
Zigbee, serial ports, MQTT or Home Assistant.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("apply.py")
spec = importlib.util.spec_from_file_location("zg204zl_reliability_apply", MODULE)
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


class VariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = Path(os.environ["ZG204ZL_SOURCE"]).resolve()

    def make_copy(self, directory):
        root = Path(directory)
        for name in fw.BASE_BLOBS:
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((self.upstream / name).read_bytes())
        return root

    def test_exact_source_blobs_and_dry_run_does_not_modify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_copy(tmp)
            originals = {n: (root / n).read_bytes() for n in fw.BASE_BLOBS}
            result = fw.apply(root, dry_run=True, check_commit=False)
            self.assertEqual(result["file_version"], "0x01413001")
            self.assertEqual(result["rejoin_delays_s"], [5, 10, 20, 45, 90, 180, 300, 600])
            self.assertEqual(result["scan_attempts"], 3)
            self.assertEqual(result["motion_rejoin_min_interval_s"], 60)
            self.assertEqual({n: (root / n).read_bytes() for n in originals}, originals)

    def test_implementation_is_board_only_and_keeps_direct_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_copy(tmp)
            result = fw.apply(root, check_commit=False)
            self.assertEqual(len(result["output_blobs"]), 6)
            zd = (root / "src/zb_appCb.c").read_text(encoding="utf-8")
            pir = (root / "src/sensor_pir.c").read_text(encoding="utf-8")
            power = (root / "src/app_cfg.h").read_text(encoding="utf-8")
            cfg = (root / "src/includes/zb_config.h").read_text(encoding="utf-8")
            version = (root / "src/version_cfg.h").read_text(encoding="utf-8")
            self.assertIn("5*1000, 10*1000, 20*1000, 45*1000, 90*1000", zd)
            self.assertIn("3*60*1000, 5*60*1000, 10*60*1000", zd)
            self.assertIn("app_rejoinOnMotion(void)", zd)
            self.assertIn("clock_time_exceed(zg204zl_motion_rejoin_tick", zd)
            self.assertIn("app_rejoinOnMotion();", pir)
            self.assertIn("remoteCmdOnOff(SENSOR_DEVICE_ENDPOINT, ZCL_CMD_ONOFF_ON)", pir)
            self.assertIn("RF_POWER_INDEX_P3p01dBm", power)
            self.assertIn("ZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t3", cfg)
            self.assertIn("#define APP_BUILD\t\t\t\t0x41", version)
            self.assertIn("zg204zl_parent_lost_or_scan_failures", zd)

    def test_fails_closed_before_writing_any_source_when_upstream_tampered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_copy(tmp)
            wrong = root / "src/zb_appCb.c"
            wrong.write_bytes(wrong.read_bytes() + b"\n/* tampered */\n")
            originals = {n: (root / n).read_bytes() for n in fw.BASE_BLOBS}
            with self.assertRaisesRegex(ValueError, "upstream blob mismatch"):
                fw.apply(root, check_commit=False)
            self.assertEqual({n: (root / n).read_bytes() for n in originals}, originals)

    def test_wrong_commit_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_copy(tmp)
            before = (root / "src/app_cfg.h").read_bytes()
            with patch.object(fw.subprocess, "check_output", return_value="unexpected source sha"):
                with self.assertRaisesRegex(ValueError, "wrong ZigbeeTLc source commit"):
                    fw.apply(root)
            self.assertEqual(before, (root / "src/app_cfg.h").read_bytes())


if __name__ == "__main__":
    unittest.main()
