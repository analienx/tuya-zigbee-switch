from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OTA_HEADER = struct.Struct("<I5HIH32sI")


def test_reseal_ota_changes_only_identity_header_bytes(tmp_path: Path):
    payload = b"\x00\x00\x10\x20\x30\x40" + b"golden-router-payload"
    source = tmp_path / "source.ota"
    output = tmp_path / "rollback.ota"
    source_version = 0x11023001
    source_image_type = 43555
    target_image_type = 65026
    target_version = 0xFFFFFFFF
    total = OTA_HEADER.size + len(payload)
    header = OTA_HEADER.pack(
        0x0BEEF11E,
        0x0100,
        OTA_HEADER.size,
        0,
        4417,
        source_image_type,
        source_version,
        2,
        b"golden" + b"\x00" * 26,
        total,
    )
    source.write_bytes(header + payload)

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/telink/make_ota.py"),
            "reseal-ota",
            "--manufacturer-id",
            "4417",
            "--source-image-type",
            str(source_image_type),
            "--source-file-version",
            hex(source_version),
            "--image-type",
            str(target_image_type),
            "--file-version",
            hex(target_version),
            str(source),
            str(output),
        ],
        check=True,
    )

    original = source.read_bytes()
    resealed = output.read_bytes()
    assert resealed[OTA_HEADER.size :] == original[OTA_HEADER.size :]
    values = OTA_HEADER.unpack(resealed[: OTA_HEADER.size])
    assert values[4] == 4417
    assert values[5] == target_image_type
    assert values[6] == target_version
    assert values[-1] == len(resealed)
    assert [i for i, (a, b) in enumerate(zip(original, resealed)) if a != b] == [
        12,
        13,
        14,
        15,
        16,
        17,
    ]


def test_reseal_ota_rejects_wrong_source_identity(tmp_path: Path):
    payload = b"payload"
    source = tmp_path / "source.ota"
    output = tmp_path / "rollback.ota"
    total = OTA_HEADER.size + len(payload)
    source.write_bytes(
        OTA_HEADER.pack(
            0x0BEEF11E, 0x0100, OTA_HEADER.size, 0, 4417, 43555, 0x11023001, 2,
            b"golden" + b"\x00" * 26, total
        ) + payload
    )
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "src/telink/make_ota.py"), "reseal-ota",
            "--manufacturer-id", "4417", "--source-image-type", "43556",
            "--source-file-version", "0x11023001", "--image-type", "65026",
            "--file-version", "0xFFFFFFFF", str(source), str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "source image type mismatch" in result.stderr
    assert not output.exists()
