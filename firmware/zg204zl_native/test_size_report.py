"""Hosted regression for the real Telink ld formatting encountered in M0."""
import tempfile
import unittest
from pathlib import Path
from size_report import report


class MapAccounting(unittest.TestCase):
    def test_counts_loaded_sections_and_not_discarded_or_bss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image.bin"
            image.write_bytes(bytes(500))
            map_path = root / "image.map"
            map_path.write_text("""Discarded input sections
 .text.unused 0x00000000 0x100 ok now generated...
./build/unused.o
Linker script and memory map
 .text.nc_send
                0x00001000 0x20 ok now generated...
./build/nc_tx.o
 .rodata        0x00001020 0x8 ./build/nc_tx.o
 .bss          0x00842100 0x100 ./build/nc_tx.o
 .text         0x00001028 0x10 ./SDK_z/lib.a(frame.o)
""")
            result = report(image, map_path)
            self.assertEqual(result["native_bytes"], 500)
            self.assertEqual(result["objects"], {
                "./build/nc_tx.o": 40, "./SDK_z/lib.a(frame.o)": 16})

    def test_refuses_a_map_without_a_loaded_section_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrong.map"
            path.write_text(" .text 0x0000 0x100 unused.o\n")
            with self.assertRaisesRegex(ValueError, "start marker"):
                report(path, path)
