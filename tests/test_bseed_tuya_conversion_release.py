from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def ota_bytes(manufacturer: int, image_type: int, version: int, marker: bytes) -> bytes:
    total = 56 + len(marker)
    header = struct.pack(
        "<I5HIH32sI",
        0x0BEEF11E,
        0x0100,
        56,
        0,
        manufacturer,
        image_type,
        version,
        2,
        b"Telink OTA Image" + b"\x00" * 16,
        total,
    )
    return header + marker


def write_target(
    directory: Path,
    *,
    board: str,
    canonical: str,
    version: int,
    custom_type: int,
    stock_name: str,
    stock_type: int = 54179,
) -> None:
    directory.mkdir(parents=True)
    marker = (board + "-payload").encode()
    normal = ota_bytes(4417, custom_type, version, marker)
    stock = ota_bytes(4417, stock_type, 0xFFFFFFFF, marker)
    (directory / "forward.ota").write_bytes(normal)
    (directory / "from_tuya.ota").write_bytes(stock)
    manifest = {
        "sourceDirty": False,
        "board": board,
        "fileVersion": version,
        "canonicalConfig": canonical,
        "stockConversion": {
            "stockManufacturerName": stock_name,
            "wrapperFileVersion": 0xFFFFFFFF,
            "headerDiffOffsets": list(range(12, 18)),
            "payloadFromByte56Identical": True,
        },
        "otaHeader": {
            "manufacturerCode": 4417,
            "imageType": custom_type,
            "fileVersion": version,
        },
        "fromTuyaOtaHeader": {
            "manufacturerCode": 4417,
            "imageType": stock_type,
            "fileVersion": 0xFFFFFFFF,
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_release_builders_generate_stock_wrappers_from_same_binary() -> None:
    for relative in (
        "make_scripts/build_bseed_ts011f_pm_v8.sh",
        "make_scripts/build_bseed_ts0726_v8.sh",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert 'FROM_TUYA_OTA="$OUT_DIR/from_tuya.ota"' in source
        assert 'OTA_IMAGE_TYPE="$STOCK_IMAGE_TYPE"' in source
        assert "OTA_VERSION=0xFFFFFFFF" in source
        assert 'normal_bytes[56:] != stock_bytes[56:]' in source
        assert "expected_offsets = list(range(12, 18))" in source
        assert '"payloadFromByte56Identical": True' in source


def test_dedicated_index_has_exact_stock_and_custom_lookup_keys(tmp_path: Path) -> None:
    pm = tmp_path / "pm"
    ts = tmp_path / "ts"
    output = tmp_path / "index_bseed.json"
    write_target(
        pm,
        board="OUTLET_BSEED_PM_TS011F",
        canonical="b28wrpvx;TS011F-BS-PM;",
        version=0x12053006,
        custom_type=43556,
        stock_name="_TZ3000_b28wrpvx",
    )
    write_target(
        ts,
        board="SWITCH_BSEED_TS0726_3GANG",
        canonical="iedhxgyi;TS0726-3-BS;",
        version=0x1102300A,
        custom_type=45577,
        stock_name="_TZ3002_iedhxgyi",
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "make_scripts/make_bseed_ota_index.py"),
            "--pm-dir",
            str(pm),
            "--ts0726-dir",
            str(ts),
            "--output",
            str(output),
            "--base-url",
            "https://example.invalid/bseed",
        ],
        check=True,
        cwd=ROOT,
    )
    entries = json.loads(output.read_text(encoding="utf-8"))
    assert len(entries) == 4
    lookup = {
        (entry["manufacturerName"][0], entry["imageType"], entry["fileVersion"])
        for entry in entries
    }
    assert lookup == {
        ("b28wrpvx", 43556, 0x12053006),
        ("_TZ3000_b28wrpvx", 54179, 0xFFFFFFFF),
        ("iedhxgyi", 45577, 0x1102300A),
        ("_TZ3002_iedhxgyi", 54179, 0xFFFFFFFF),
    }
    assert all(entry["url"].startswith("https://example.invalid/bseed/") for entry in entries)


def test_generic_index_replaces_stale_bseed_and_removes_force_entries(tmp_path: Path) -> None:
    bseed_path = tmp_path / "bseed.json"
    router_path = tmp_path / "router.json"
    force_path = tmp_path / "force.json"
    current = [
        {"manufacturerName": ["b28wrpvx"], "imageType": 43556, "fileVersion": 1},
        {"manufacturerName": ["_TZ3000_b28wrpvx"], "imageType": 54179, "fileVersion": 0xFFFFFFFF},
        {"manufacturerName": ["iedhxgyi"], "imageType": 45577, "fileVersion": 2},
        {"manufacturerName": ["_TZ3002_iedhxgyi"], "imageType": 54179, "fileVersion": 0xFFFFFFFF},
    ]
    unrelated = {"manufacturerName": ["other"], "imageType": 123, "fileVersion": 5}
    stale = {"manufacturerName": ["_TZ3000_b28wrpvx", "b28wrpvx"], "imageType": 54179, "fileVersion": 99}
    bseed_path.write_text(json.dumps(current), encoding="utf-8")
    router_path.write_text(json.dumps([unrelated, stale]), encoding="utf-8")
    force_path.write_text(json.dumps([unrelated, stale]), encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "make_scripts/merge_bseed_ota_indexes.py"),
            "--bseed-index",
            str(bseed_path),
            "--router-index",
            str(router_path),
            "--force-index",
            str(force_path),
        ],
        check=True,
        cwd=ROOT,
    )
    router = json.loads(router_path.read_text(encoding="utf-8"))
    force = json.loads(force_path.read_text(encoding="utf-8"))
    assert router == [unrelated] + current
    assert force == [unrelated]
