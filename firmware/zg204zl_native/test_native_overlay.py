"""Hosted source-integrity tests; no live device or local executor."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import apply_native as overlay
import specialize_ota


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

    def test_client_only_sdk_retains_client_receive_and_download_implementations(self):
        source = Path(os.environ['ZG204ZL_SOURCE'])
        with zipfile.ZipFile(source / 'tools/SDK_z.zip') as archive:
            raw = {name: archive.read(name) for name in specialize_ota.PINS}
        output = specialize_ota.sources(raw)
        for name in raw:
            original = raw[name].decode().replace('\r', '')
            modified = output[name].decode()
            if name.endswith('/ota.c'):
                # Every function before the dispatch remains byte-for-byte identical
                # except the two explicit init/query role checks above.
                start = original.rindex('static status_t ota_imageNotifyHandler(')
                end = original.index('status_t zcl_otaCb(')
                self.assertEqual(original[start:end], modified[modified.rindex(
                    'static status_t ota_imageNotifyHandler('):modified.index('status_t zcl_otaCb(')])
                server_cases = 'switch (cmdId) {\n            case ZCL_CMD_OTA_IMAGE_NOTIFY:'
                self.assertIn(server_cases, modified)
            else:
                start = original.index('_CODE_ZCL_ static status_t zcl_ota_serverCmdHandler(')
                end = original.index('_CODE_ZCL_ static status_t zcl_ota_cmdHandler(', start)
                self.assertEqual(original[start:end], modified[start:end])
            tampered = dict(raw); tampered[name] += b'\n'
            with self.assertRaisesRegex(ValueError, 'SDK hash mismatch'):
                specialize_ota.sources(tampered)
