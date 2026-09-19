import struct
import unittest

from helper_scripts.ts0505b.parse_gbl import find_gbl, parse_gbl


class ParseGblTests(unittest.TestCase):
    def make_gbl(self, gbl_type=0, include_signature=False):
        parts = []
        parts.append(struct.pack("<II", 0x03A617EB, 8) + struct.pack("<II", 0x03000000, gbl_type))
        app_payload = struct.pack("<III", 1, 0x71, 0) + bytes(range(16))
        parts.append(struct.pack("<II", 0xF40A0AF4, len(app_payload)) + app_payload)
        prog_payload = struct.pack("<I", 0x00004000) + b"APP"
        parts.append(struct.pack("<II", 0xFE0101FE, len(prog_payload)) + prog_payload)
        if include_signature:
            signature = b"S" * 64
            parts.append(struct.pack("<II", 0xF70A0AF7, len(signature)) + signature)
        end_payload = struct.pack("<I", 0)
        parts.append(struct.pack("<II", 0xFC0404FC, len(end_payload)) + end_payload)
        return b"".join(parts)

    def test_plain_gbl(self):
        parsed = parse_gbl(self.make_gbl())
        self.assertFalse(parsed["signed_flag"])
        self.assertFalse(parsed["encrypted_flag"])
        self.assertTrue(parsed["has_end_tag"])
        self.assertEqual(parsed["application_info"]["application_version"], 0x71)
        self.assertEqual(parsed["program_ranges"][0]["flash_start_address"], 0x4000)
        self.assertEqual(parsed["program_ranges"][0]["flash_end_address_exclusive"], 0x4003)

    def test_eraseprog_is_plain_program_data_with_exact_range(self):
        gbl = self.make_gbl()
        erase_payload = struct.pack("<I", 0x00004238) + b"ERASEPROG"
        marker = struct.pack("<II", 0xFE0101FE, 7) + struct.pack("<I", 0x00004000) + b"APP"
        replacement = struct.pack("<II", 0xFD0303FD, len(erase_payload)) + erase_payload
        parsed = parse_gbl(gbl.replace(marker, replacement))
        item = parsed["program_ranges"][0]
        self.assertEqual(item["tag"], "erase_program_data")
        self.assertEqual(item["encoding"], "plain")
        self.assertTrue(item["erase_before_program"])
        self.assertEqual(item["flash_start_address"], 0x4238)
        self.assertEqual(item["flash_end_address_exclusive"], 0x4238 + len(b"ERASEPROG"))

    def test_signed_flag_and_signature_tag(self):
        parsed = parse_gbl(self.make_gbl(gbl_type=0x100, include_signature=True))
        self.assertTrue(parsed["signed_flag"])
        self.assertTrue(parsed["has_signature_tag"])

    def test_find_embedded_gbl(self):
        wrapped = b"WRAPPER" + self.make_gbl()
        self.assertEqual(find_gbl(wrapped), len(b"WRAPPER"))
        parsed = parse_gbl(wrapped, len(b"WRAPPER"))
        self.assertEqual(parsed["gbl_offset"], len(b"WRAPPER"))


if __name__ == "__main__":
    unittest.main()
