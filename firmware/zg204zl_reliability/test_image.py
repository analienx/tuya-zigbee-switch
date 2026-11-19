"""Corruption tests for the same validator used by hosted artifact creation."""
import binascii
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('zg_image', Path(__file__).with_name('verify_image.py'))
image_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_gate)


class ImageGate(unittest.TestCase):
    def test_valid_payload_and_independent_corruptions(self):
        body = bytearray(4092)
        body[6:8] = b'\x5d\x02'
        body[8:12] = b'KNLT'
        struct.pack_into('<I', body, 2, image_gate.VERSION)
        struct.pack_into('<HH', body, 18, image_gate.MFG, image_gate.IMAGE_TYPE)
        struct.pack_into('<I', body, 24, 4096)
        firmware = bytes(body) + struct.pack('<I', binascii.crc32(body) ^ 0xffffffff)
        header = struct.pack('<I5HIH32sI', 0x0BEEF11E, 0x100, 56, 0,
                             image_gate.MFG, image_gate.IMAGE_TYPE, image_gate.VERSION,
                             2, b'ZG204ZL'.ljust(32, b'\0'), len(firmware) + 62)
        ota = header + struct.pack('<HI', 0, len(firmware)) + firmware
        with tempfile.TemporaryDirectory() as tmp:
            native_path, ota_path = Path(tmp) / 'image.bin', Path(tmp) / 'image.ota'
            native_path.write_bytes(firmware)
            ota_path.write_bytes(ota)
            self.assertEqual(image_gate.verify(native_path, ota_path)['native']['bytes'], 4096)
            for name, native, package in (
                ('crc', firmware[:-1] + bytes([firmware[-1] ^ 1]), ota),
                ('identity', firmware, ota[:14] + b'\xff\xff\xff\xff' + ota[18:]),
                ('payload', firmware, ota[:-1] + bytes([ota[-1] ^ 1])),
                ('oversize', firmware + b'\0' * 0x20000, ota),
                ('truncated', firmware[:10], ota),
            ):
                with self.subTest(name=name):
                    native_path.write_bytes(native)
                    ota_path.write_bytes(package)
                    with self.assertRaises(ValueError):
                        image_gate.verify(native_path, ota_path)


if __name__ == '__main__':
    unittest.main()
