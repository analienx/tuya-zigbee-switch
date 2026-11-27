"""Hosted tests compile the same native modules copied into the TC32 image."""
import ctypes as C
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'native.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-std=c99', '-Os', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'native'), *map(str, sorted((ROOT / 'native').glob('nc_[ct]*.c'))),
                        str(ROOT / 'host/nc_port_host.c'), '-o', str(lib)], check=True)
        cls.fw = C.CDLL(str(lib))
        cls.fw.nc_configure.argtypes = [C.c_void_p, C.c_uint]
        cls.fw.nc_time_response.argtypes = [C.c_uint8, C.c_void_p, C.c_uint]
        cls.fw.host_time.argtypes = cls.fw.host_advance.argtypes = [C.c_uint32]

    def setUp(self):
        self.fw.host_reset()

    def config(self, flags=0, start=1080, end=420, level=152, fade_in=15, fade_out=30):
        raw = struct.pack('<4B4H', 1, flags, level, 0, start, end, fade_in, fade_out)
        return self.fw.nc_configure(raw, len(raw))

    def frame(self, i):
        out = C.create_string_buffer(32)
        n = self.fw.host_frame(i, out)
        return out.raw[:n]

    def event(self, name):
        getattr(self.fw, name)()
        self.fw.nc_service()

    def test_native_fade_and_legacy_frames(self):
        self.assertEqual(self.config(), 0)
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.event('nc_motion')
        self.assertEqual(self.frame(0), bytes.fromhex('110004980f00'))
        self.fw.host_ack(0, 0)
        self.event('nc_absence')
        self.assertEqual(self.frame(1), bytes.fromhex('110104001e00'))
        self.fw.host_reset()
        self.fw.host_bind(0, 123, 1, 1, 0)
        self.event('nc_motion')
        self.assertEqual(self.frame(0), bytes.fromhex('010001'))

    def test_level_requires_committed_config_and_deduplicates(self):
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.fw.host_bind(1, 123, 1, 1, 0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_cluster(0), 6)
        self.assertEqual(self.fw.host_count(), 1)

    def test_config_validation_and_idempotence(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.fw.host_writes(), 1)
        self.assertEqual(self.config(fade_out=1201), 0x87)
        self.assertEqual(self.config(flags=1, start=0, end=0), 0x87)
        self.assertEqual(self.config(level=255), 0x87)
        self.fw.host_lux_target(65535)
        self.assertEqual(self.config(flags=2), 0x87)

    def test_bright_motion_is_reportable_but_does_not_send(self):
        self.config(flags=2)
        self.fw.host_bind(0, 123, 1, 2, 0)
        self.fw.host_dark(0)
        self.event('nc_motion')
        self.assertEqual(self.fw.host_count(), 0)
        self.assertEqual(self.fw.host_reason(), 5)
        self.assertEqual(self.fw.host_lux_reads(), 1)

    def test_actual_runtime_fits_budget(self):
        self.assertLessEqual(self.fw.host_size() + 16, 512)


if __name__ == '__main__':
    unittest.main()
