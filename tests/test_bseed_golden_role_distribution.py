"""Fail closed: normal BSEED OTA updates must not silently change device roles."""
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OTA = ROOT / "zigbee2mqtt" / "ota"
GOLDEN = {
    "ts011f-pm-v12053007.ota": (302329863, 43556, "b28wrpvx"),
    "ts011f-nonpm-v11023001.ota": (285356033, 43555, "o1jzcxou"),
    "ts0726-v1102300a.ota": (285356042, 45577, "iedhxgyi"),
}


def test_only_hardware_accepted_router_images_are_on_normal_index():
    entries = json.loads((OTA / "index_bseed.json").read_text(encoding="utf-8-sig"))
    assert len(entries) == 6, "only three golden Routers and three stock conversions are released"
    assert len({(x["imageType"], tuple(x["manufacturerName"])) for x in entries}) == 6
    for filename, (version, image_type, manufacturer) in GOLDEN.items():
        native = next(x for x in entries if x["fileName"] == filename)
        stock = next(x for x in entries if x["fileName"] == filename.replace(".ota", "-from_tuya.ota"))
        for item, expected_version, expected_type in ((native, version, image_type), (stock, 0xFFFFFFFF, 54179)):
            image = OTA / "bseed" / item["fileName"]
            payload = image.read_bytes()
            magic, _, _, _, code, actual_type, actual_version, _, _, size = struct.unpack("<I5HIH32sI", payload[:56])
            assert (magic, code, actual_type, actual_version, size) == (
                0x0BEEF11E, 4417, expected_type, expected_version, len(payload)
            )
            assert item["fileVersion"] == expected_version
            assert item["imageType"] == expected_type
            assert item["manufacturerCode"] == 4417
            assert item["sha512"] == hashlib.sha512(payload).hexdigest()
            assert item["url"].endswith("/zigbee2mqtt/ota/bseed/" + item["fileName"])
        assert native["manufacturerName"] == [manufacturer]
        stock_prefix = "_TZ3002_" if image_type == 45577 else "_TZ3000_"
        assert stock["manufacturerName"] == [stock_prefix + manufacturer]
        assert (OTA / "bseed" / native["fileName"]).read_bytes()[56:] == (OTA / "bseed" / stock["fileName"]).read_bytes()[56:]


def test_unaccepted_clients_are_not_publicly_advertised():
    assert not (OTA / "index_bseed_to_client.json").exists()
    assert not (OTA / "index_bseed_to_router.json").exists()
    entries = json.loads((OTA / "index_bseed.json").read_text(encoding="utf-8-sig"))
    assert not ({65024, 65025, 65026} & {x["imageType"] for x in entries})
    assert all("client" not in x["fileName"].lower() for x in entries)
