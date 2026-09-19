import struct
import unittest

from helper_scripts.ts0505b.parse_zigbee_ota import OTA_MAGIC, parse_ota_header


class ParseOtaHeaderTests(unittest.TestCase):
    def make_image(self, *, field_control=0, optional=b"", manufacturer=0x1002, image_type=0x1602):
        header_length = 56 + len(optional)
        body = b"PAYLOAD"
        total = header_length + len(body)
        header = struct.pack(
            "<IHHHHHIH",
            OTA_MAGIC,
            0x0100,
            header_length,
            field_control,
            manufacturer,
            image_type,
            0x00000071,
            0x0002,
        )
        header += b"TS0505B-router-test".ljust(32, b"\x00")
        header += struct.pack("<I", total)
        return header + optional + body

    def test_base_header(self):
        parsed = parse_ota_header(self.make_image())
        self.assertEqual(parsed["manufacturer_code"], 0x1002)
        self.assertEqual(parsed["image_type"], 0x1602)
        self.assertEqual(parsed["file_version"], 0x71)
        self.assertTrue(parsed["declared_size_matches_file"])

    def test_hardware_version_fields(self):
        image = self.make_image(field_control=0x0004, optional=struct.pack("<HH", 0, 5))
        parsed = parse_ota_header(image)
        self.assertEqual(parsed["minimum_hardware_version"], 0)
        self.assertEqual(parsed["maximum_hardware_version"], 5)

    def test_bad_magic_rejected(self):
        image = bytearray(self.make_image())
        image[0:4] = b"NOPE"
        with self.assertRaises(ValueError):
            parse_ota_header(bytes(image))

    def test_short_file_rejected(self):
        with self.assertRaises(ValueError):
            parse_ota_header(b"tiny")


if __name__ == "__main__":
    unittest.main()
