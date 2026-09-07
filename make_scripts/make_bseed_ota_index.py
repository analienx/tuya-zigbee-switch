#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


TARGETS = {
    "OUTLET_BSEED_PM_TS011F": "ts011f-pm",
    "SWITCH_BSEED_TS0726_3GANG": "ts0726",
}


def load_manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("board") not in TARGETS:
        raise SystemExit(f"unsupported BSEED board in {path}: {manifest.get('board')}")
    if manifest.get("sourceDirty"):
        raise SystemExit(f"refusing dirty-source manifest: {path}")
    conversion = manifest.get("stockConversion") or {}
    if conversion.get("wrapperFileVersion") != 0xFFFFFFFF:
        raise SystemExit(f"invalid stock wrapper version in {path}")
    if conversion.get("payloadFromByte56Identical") is not True:
        raise SystemExit(f"stock wrapper payload proof missing in {path}")
    if conversion.get("headerDiffOffsets") != list(range(12, 18)):
        raise SystemExit(f"unexpected stock wrapper header diff in {path}")
    return manifest


def index_entry(path: Path, public_name: str, base_url: str, manufacturer_name: str) -> dict:
    data = path.read_bytes()
    if len(data) < 56:
        raise SystemExit(f"OTA too small: {path}")
    if int.from_bytes(data[0:4], "little") != 0x0BEEF11E:
        raise SystemExit(f"bad OTA magic: {path}")
    total = int.from_bytes(data[52:56], "little")
    if total != len(data):
        raise SystemExit(f"OTA size/header mismatch: {path}: {total} != {len(data)}")
    return {
        "fileName": public_name,
        "fileVersion": int.from_bytes(data[14:18], "little"),
        "fileSize": len(data),
        "url": f"{base_url.rstrip('/')}/{public_name}",
        "imageType": int.from_bytes(data[12:14], "little"),
        "manufacturerCode": int.from_bytes(data[10:12], "little"),
        "sha512": hashlib.sha512(data).hexdigest(),
        "otaHeaderString": data[20:52].decode("latin1"),
        "manufacturerName": [manufacturer_name],
    }


def make_entries(directory: Path) -> list[dict]:
    manifest = load_manifest(directory)
    board = manifest["board"]
    slug = TARGETS[board]
    version_hex = f"{int(manifest['fileVersion']):08x}"
    custom_name = str(manifest["canonicalConfig"]).split(";", 1)[0]
    stock_name = manifest["stockConversion"]["stockManufacturerName"]

    normal = directory / "forward.ota"
    stock = directory / "from_tuya.ota"
    normal_public = f"{slug}-v{version_hex}.ota"
    stock_public = f"{slug}-v{version_hex}-from_tuya.ota"

    entries = [
        index_entry(normal, normal_public, ARGS.base_url, custom_name),
        index_entry(stock, stock_public, ARGS.base_url, stock_name),
    ]

    normal_header = manifest["otaHeader"]
    stock_header = manifest["fromTuyaOtaHeader"]
    if entries[0]["manufacturerCode"] != normal_header["manufacturerCode"]:
        raise SystemExit(f"normal manifest/index manufacturer mismatch for {board}")
    if entries[0]["imageType"] != normal_header["imageType"]:
        raise SystemExit(f"normal manifest/index image type mismatch for {board}")
    if entries[0]["fileVersion"] != normal_header["fileVersion"]:
        raise SystemExit(f"normal manifest/index version mismatch for {board}")
    if entries[1]["manufacturerCode"] != stock_header["manufacturerCode"]:
        raise SystemExit(f"stock manifest/index manufacturer mismatch for {board}")
    if entries[1]["imageType"] != stock_header["imageType"]:
        raise SystemExit(f"stock manifest/index image type mismatch for {board}")
    if entries[1]["fileVersion"] != 0xFFFFFFFF:
        raise SystemExit(f"stock conversion index version must be 0xFFFFFFFF for {board}")
    return entries


def assert_unique(entries: list[dict]) -> None:
    seen: set[tuple[int, int, str]] = set()
    for entry in entries:
        names = entry.get("manufacturerName") or []
        if len(names) != 1:
            raise SystemExit(f"BSEED index entries must have one exact manufacturerName: {entry}")
        key = (entry["manufacturerCode"], entry["imageType"], names[0])
        if key in seen:
            raise SystemExit(f"duplicate BSEED OTA lookup key: {key}")
        seen.add(key)


parser = argparse.ArgumentParser(description="Generate the dedicated BSEED Zigbee2MQTT OTA index")
parser.add_argument("--pm-dir", type=Path, required=True)
parser.add_argument("--ts0726-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument(
    "--base-url",
    default="https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/bseed",
)
ARGS = parser.parse_args()

entries = make_entries(ARGS.pm_dir) + make_entries(ARGS.ts0726_dir)
assert_unique(entries)
ARGS.output.parent.mkdir(parents=True, exist_ok=True)
ARGS.output.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
print(f"wrote {len(entries)} exact BSEED OTA entries to {ARGS.output}")
