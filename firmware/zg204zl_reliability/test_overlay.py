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
import subprocess
import json
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
            self.assertEqual(result["file_version"], "0x01433001")
            self.assertEqual(result["rejoin_delays_s"], [2, 3, 5, 10, 20, 40, 80, 300, 900, 1800])
            self.assertEqual(result["scan_attempts"], 3)
            self.assertEqual(result["motion_rejoin_min_interval_s"], 300)
            self.assertEqual(result["tx_power_new_dbm_nominal"], 6.14)
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
            self.assertIn("2000, 3000, 5000, 10000, 20000, 40000, 80000, 300000, 900000, 1800000", zd)
            self.assertIn("app_rejoinOnMotion(void)", zd)
            self.assertNotIn("clock_time_exceed", zd)
            self.assertEqual(zd.count('app_motionCooldownClear();'), 3)
            self.assertIn('g_sensorAppCtx.linkDown = 1;\n        rj_secMode = REJOIN_INSECURITY;', zd)
            self.assertIn("app_rejoinOnMotion();", pir)
            self.assertIn("remoteCmdOnOff(SENSOR_DEVICE_ENDPOINT, ZCL_CMD_ONOFF_ON)", pir)
            self.assertIn('#define ZB_TX_POWER_IDX_DEF\t\t\t\tRF_POWER_INDEX_P6p14dBm', power)
            self.assertIn("ZDO_NWK_SCAN_ATTEMPTS\t\t\t\t\t\t3", cfg)
            self.assertIn("#define APP_BUILD\t\t\t\t0x43", version)
            self.assertIn('zb_isUnderRejoinMode()', zd)

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


class SchedulerBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        upstream = Path(os.environ['ZG204ZL_SOURCE']).resolve()
        original = (upstream / 'src/zb_appCb.c').read_text()
        changed = fw.modified_sources(upstream)['src/zb_appCb.c'].decode()
        end = '#if !USE_BLE\nstatic ev_timer_event_t *timerDevAnnceEvt;'
        sections = {
            'baseline': original[original.index('/* Rejoin period, ms.'):original.index(end)],
            'candidate': changed[changed.index('/* Injected only'):changed.index(end)],
        }
        cls.binaries = {}
        for name, section in sections.items():
            folder = root / name
            folder.mkdir()
            (folder / 'scheduler_under_test.inc').write_text(section)
            binary = folder / 'scheduler'
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-function', '-Wno-unused-parameter',
                            '-Wno-unused-variable', '-Wno-sign-compare',
                            '-DBASELINE=' + ('1' if name == 'baseline' else '0'),
                            '-I' + str(folder), str(MODULE.with_name('scheduler_harness.c')),
                            '-o', str(binary)], check=True)
            cls.binaries[name] = binary

    def test_actual_c_scheduler_behaviors(self):
        for scenario in ('schedule', 'motion_wrap', 'busy', 'success', 'pool', 'button',
                         'shared_floor', 'unknown_channel', 'counter_wrap'):
            with self.subTest(scenario=scenario):
                subprocess.run([str(self.binaries['candidate']), scenario], check=True, timeout=10)

    def test_eight_hour_outage_request_budget(self):
        results = {}
        for name, binary in self.binaries.items():
            results[name] = {}
            for mode in ('quiet', 'motion'):
                output = subprocess.check_output([str(binary), mode], text=True, timeout=10)
                results[name][mode] = json.loads(output)
        self.assertEqual(results['baseline']['quiet']['requests'], 21)
        self.assertEqual(results['candidate']['quiet']['requests'], 25)
        # Motion does not reset the sequence or starve the full-channel mask.
        moving = results['candidate']['motion']
        self.assertLessEqual(moving['requests'], 110)
        self.assertGreater(moving['requests'], results['candidate']['quiet']['requests'])
        self.assertGreaterEqual(moving['full_scans'], (moving['requests'] - 2) // 2)
        self.assertGreater(moving['secure_full_scans'], 0)
        self.assertGreater(moving['insecure_full_scans'], 0)
        evidence = os.environ.get('ZG204ZL_BATTERY_MODEL')
        # Sensitivity examples only, including higher average active current.
        # These assumptions are not a measurement or a bound for the 2.1 V PCB.
        baseline_mah = results['baseline']['quiet']['requests'] * 8 / 3600
        charge_examples = {}
        for mode, counts in results['candidate'].items():
            charge_examples[mode] = [
                {'active_ma': ma, 'active_s_per_request': seconds,
                 'extra_mah': counts['requests'] * ma * seconds / 3600 - baseline_mah}
                for ma, seconds in ((8, 1), (16, 3))
            ]
        if evidence:
            Path(evidence).write_text(json.dumps({
                'assumptions': '8 hours; accepted requests last 2 s; known channel 11; all-channel mask 11..26; motion every 60 s; no RF model or measured current',
                'results': results,
                'charge_sensitivity': {
                    'baseline_request_assumption': '8 mA for 1 second per upstream request',
                    'candidate_assumptions': '8 mA for 1 second OR 16 mA for 3 seconds per request; not measured and not bounds',
                    'extra_mah_per_eight_hour_outage': charge_examples,
                },
            }, indent=2) + '\n')
        print('Battery request-count model:', json.dumps(results))


if __name__ == "__main__":
    unittest.main()
