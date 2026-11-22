"""Fast LED batch selector must never admit old/stock firmware or wrong hardware."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_led_brightness as led


def socket(name, build="1.2.5-bseedr24", model="TS011F-BS-PM",
           role="Router", manufacturer="b28wrpvx", ieee="0xa4c1381234567890"):
    return {
        "friendly_name": name, "software_build_id": build,
        "model_id": model, "type": role,
        "manufacturer": manufacturer, "ieee_address": ieee,
        "interview_state": "SUCCESSFUL",
        "definition": {"exposes": [{
            "type": "composite", "features": [{
                "property": "led_brightness_switch", "name": "led_brightness",
                "access": 7, "type": "numeric", "value_min": 1,
                "value_max": 100,
            }],
        }]},
    }


@pytest.mark.parametrize("build,model,role,manufacturer", [
    ("1.2.5-bseedr24", "TS011F-BS-PM", "Router", "b28wrpvx"),
    ("1.2.5-bseedcli27", "TS011F-BS-PM", "EndDevice", "b28wrpvx"),
    ("1.1.3-bseedr21", "TS011F-BS", "Router", "o1jzcxou"),
    ("1.1.3-bseedc19", "TS011F-BS", "EndDevice", "o1jzcxou"),
    ("1.2.5-bseedr25", "TS011F-BS-PM", "Router", "b28wrpvx"),
])
def test_supported_variants(build, model, role, manufacturer):
    assert led.classify(socket("a", build, model, role, manufacturer))


@pytest.mark.parametrize("mutate", [
    {"software_build_id": None},
    {"software_build_id": "1.2.5-bseedr23"},
    {"software_build_id": "1.1.3-bseedc17", "model_id": "TS011F-BS",
     "manufacturer": "o1jzcxou", "type": "EndDevice"},
    {"manufacturer": "_TZ3000_b28wrpvx"},
    {"model_id": "TS011F"},
    {"type": "EndDevice"},
    {"interview_state": "FAILED"},
    {"ieee_address": "invalid"},
    {"definition": {"exposes": []}},
    {"definition": {"exposes": [{"name": "led_brightness",
                                 "property": "led_brightness",
                                 "access": 7}]}},
    {"definition": {"exposes": [{"name": "led_brightness",
                                 "property": "led_brightness_switch",
                                 "access": 1}]}},
])
def test_rejects_wrong_identity_old_build_or_unwriteable_setting(mutate):
    target = socket("a")
    target.update(mutate)
    assert led.classify(target) is None


def test_only_latest_installed_per_family_by_default():
    old = socket("old", ieee="0xa4c1380000000001")
    new = socket("new", build="1.2.5-bseedr25",
                 ieee="0xa4c1380000000002")
    nonpm = socket("nonpm", "1.1.3-bseedc19", "TS011F-BS",
                   "EndDevice", "o1jzcxou", ieee="0xa4c1380000000003")
    chosen = led.targets_from_inventory([old, new, nonpm])
    assert {d["friendly_name"] for d in chosen} == {"new", "nonpm"}
    compatible = led.targets_from_inventory([old, new, nonpm],
                                             include_compatible=True)
    assert {d["friendly_name"] for d in compatible} == {"old", "new", "nonpm"}


def test_exact_named_selection_disallows_stock_or_older():
    old = socket("old", ieee="0xa4c1380000000001")
    new = socket("new", build="1.2.5-bseedr25",
                 ieee="0xa4c1380000000002")
    with pytest.raises(ValueError):
        led.targets_from_inventory([old, new], only=["old"])
    with pytest.raises(ValueError):
        led.targets_from_inventory([old, new], only=["not_a_socket"])
    assert led.targets_from_inventory([old, new], only=["new"]) == [new]


def test_duplicate_ieee_fails_closed():
    a = socket("a")
    b = socket("b")
    with pytest.raises(ValueError, match="Duplicate"):
        led.targets_from_inventory([a, b])


def test_network_ota_lock_for_target_skips(tmp_path):
    assert led.active_target_ota(tmp_path, "0xa4c1381234567890") is False
    (tmp_path / "bseed-zigbee-test.json").write_text(
        json.dumps({"ieee": "0xa4c1381234567890"}), encoding="utf8")
    assert led.active_target_ota(tmp_path, "0xa4c1381234567890") is True
    assert led.active_target_ota(tmp_path, "0xa4c1380000000000") is False


def test_corrupted_authority_lock_fails_closed(tmp_path):
    (tmp_path / "bseed-zigbee-test.json").write_text("not-json", encoding="utf8")
    assert led.active_target_ota(tmp_path, "0xa4c1381234567890") is True


def test_missing_network_authority_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="Missing"):
        led.active_target_ota(tmp_path / "missing", "0xa4c1381234567890")


def test_mqtt_set_command_accepts_only_led_brightness():
    class Receipt:
        rc = 0
        def wait_for_publish(self, timeout):
            assert timeout == 5
        def is_published(self):
            return True

    class Publisher:
        def __init__(self):
            self.published = []
        def publish(self, topic, payload, qos, retain):
            self.published.append((topic, json.loads(payload), qos, retain))
            return Receipt()

    class Stub:
        base = "zigbee2mqtt"
        client = Publisher()

    b = Stub()
    led.Bridge.publish(b, "BedroomSocketCabinetL", "set",
                       {"led_brightness_switch": 30})
    assert b.client.published == [
        ("zigbee2mqtt/BedroomSocketCabinetL/set",
         {"led_brightness_switch": 30}, 1, False),
    ]
    for bad in [
        {"state": "OFF"},
        {"state_relay": "OFF"},
        {"relay_physical_mode": "always_off"},
        {"led_brightness": 30},
        {"led_brightness_switch": 0},
        {"led_brightness_switch": 101},
        {"led_brightness_switch": True},
        {"led_brightness_switch": "30"},
        {"led_brightness_switch": 30, "state": "OFF"},
    ]:
        with pytest.raises(ValueError):
            led.Bridge.publish(b, "BedroomSocketCabinetL", "set", bad)
    assert len(b.client.published) == 1
