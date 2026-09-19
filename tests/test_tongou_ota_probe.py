from helper_scripts.capture_tongou_ota_tuple import extract_target_ota_tuples


def test_extract_target_ota_tuple_ignores_concurrent_device():
    events = [
        {"raw": (
            "Received Zigbee message from 'OtherSampleDevice' "
            'data \'{"fieldControl":0,"fileVersion":620834817,'
            '"imageType":5142,"manufacturerCode":4687}\''
        )},
        {"raw": (
            "zh:controller: Received payload "
            '"manufacturerCode":4687,"imageType":5142,'
            '"fileVersion":620834817'
        )},
        {"raw": (
            "z2m: Received Zigbee message from 'BreakerSampleA' "
            'data \'{"fieldControl":0,"fileVersion":75,'
            '"imageType":5634,"manufacturerCode":4098}\''
        )},
    ]

    assert extract_target_ota_tuples(events, "BreakerSampleA") == [
        {"manufacturerCode": 4098, "imageType": 5634, "fileVersion": 75}
    ]


def test_saved_mqtt_capture_decodes_escaped_json_and_excludes_other_devices():
    import json

    target = "TongouUnderTest"
    ota_message = (
        "z2m: Received Zigbee message from 'TongouUnderTest', "
        "type 'commandQueryNextImageRequest', cluster 'genOta', "
        "data '{\"fieldControl\":0,\"fileVersion\":75,"
        "\"imageType\":5634,\"manufacturerCode\":4098}' from endpoint 1"
    )
    unrelated = ota_message.replace("TongouUnderTest", "OtherDevice").replace(
        '"fileVersion":75', '"fileVersion":620834817'
    )
    events = [
        {"level": "debug", "message": unrelated},
        {"level": "debug", "message": ota_message},
        {"raw": json.dumps({"level": "debug", "message": ota_message})},
    ]
    assert extract_target_ota_tuples(events, target) == [
        {"manufacturerCode": 4098, "imageType": 5634, "fileVersion": 75}
    ]


def test_offline_reparse_has_no_network_dependency(tmp_path):
    import json
    from helper_scripts.reparse_tongou_ota_capture import reparse

    target = "TongouUnderTest"
    message = (
        "z2m: Received Zigbee message from 'TongouUnderTest', "
        "type 'commandQueryNextImageRequest', cluster 'genOta', "
        "data '{\"fieldControl\":0,\"manufacturerCode\":4098,"
        "\"imageType\":5634,\"fileVersion\":75}' from endpoint 1"
    )
    capture = tmp_path / "offline.json"
    capture.write_text(json.dumps({"target": target, "logs": [
        {"level": "debug", "message": message}
    ]}), encoding="utf-8")
    assert reparse(capture) == {"ota_tuples": [
        {"manufacturerCode": 4098, "imageType": 5634, "fileVersion": 75}
    ]}


def test_live_probe_requires_explicit_network_change_opt_in(tmp_path):
    """A casual invocation must fail before accessing config, MQTT or Z2M."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "helper_scripts/capture_tongou_ota_tuple.py",
         "--config", str(tmp_path / "missing-config.yaml"),
         "--target", "BreakerSampleA", "--output", str(tmp_path / "capture.json")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0
    assert "disabled by default" in proc.stderr
    assert not (tmp_path / "capture.json").exists()
