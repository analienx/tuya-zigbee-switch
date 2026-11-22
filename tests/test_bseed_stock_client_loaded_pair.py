"""Exact loaded stock-to-Client canary: all board/IEEE/load/relay risk pins."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_stock_client_loaded_canary as authority
import bseed_socket_migrate as migrate
import bseed_ota_campaign as campaign
import bseed_pm_meter_waiver as waiver
import bseed_relay_state_policy as relay


def profile(tmp_path, name="HALLSocketMain"):
    site = {
        "stock_loaded_preserve_devices": list(authority.TARGETS),
        "publish": {"url_base": "http://example.invalid/bseed"},
        "serve_root": str(tmp_path),
        "broker": "127.0.0.1", "mqtt_config": str(tmp_path / "mqtt.yaml"),
        "network_lock_dir": str(tmp_path / "locks"),
        "network_id": "0x00124b002d12b1fd",
        "pm_ssh_host": "127.0.0.1", "pm_ssh_key": str(tmp_path / "ssh"),
    }
    live = dict(friendly_name=name, ieee_address=authority.TARGETS[name],
                manufacturer="_TZ3000_b28wrpvx", model_id="TS011F",
                type="Router", software_build_id=None)
    entry = dict(build="1.2.5-bseedcli27", imageType=65024,
                 manufacturerCode=4417, fileVersion=0x12053026)
    route = migrate.choose_route(live, "client")
    return migrate.build_profile(
        site, live, route, entry, image=tmp_path / "wrapper.ota",
        sha256=authority.TRANSPORT_SHA, native_image=tmp_path / "native.ota",
        native_sha256=authority.NATIVE_SHA, join_via="AlternativeRouter",
        workdir=tmp_path / name, folder="fixture",
    )


@pytest.mark.parametrize("name", list(authority.TARGETS))
def test_exact_two_loaded_candidates_pass_profile_and_meter_waiver(tmp_path, name):
    p = profile(tmp_path, name)
    assert authority.authorized(p) is True
    assert waiver.validate(p) is True
    assert relay.validate(p) is True
    assert p["auto_relay_off"] is False
    assert p["relay_get_key"] == "state"
    assert p["expect_relay"] == "ON"
    assert p["pm_preflash_load_proof"] == "meter"
    assert p["operator_waive_preflash_pm"] is True
    assert "--waive-preflash-pm" in campaign.runner_args(p, "flash")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(p), encoding="utf8")
    assert campaign.load_profile(path)["stock_client_preserve_loaded"] is True


@pytest.mark.parametrize("key,value", [
    ("ieee", "0xa4c13812088a54ac"), ("manufacturer", "_TZ3000_other"),
    ("model", "TS011F-A"), ("postflash_role", "Router"),
    ("preflash_role", "EndDevice"), ("postflash_build", "1.2.5-bseedcli26"),
    ("image_type", 65024), ("file_version", "0x12053026"),
    ("manufacturer_code", 999), ("sha256", "0" * 64),
    ("native_sha256", "0" * 64), ("auto_relay_off", True),
    ("expect_relay", "OFF"), ("relay_state_policy", None),
    ("relay_get_key", "state_relay"), ("pm_preflash_load_proof", "physically_unloaded"),
    ("operator_waive_preflash_pm", False), ("operator_connected_load_risk_accepted", False),
    ("stock_client_conversion", False), ("stock_client_preserve_loaded", "true"),
    ("non_pm", True), ("force_test_transition", True),
    ("preflash_build", "stock-guess"),
])
def test_loaded_canary_refuses_any_identity_or_safety_mutation(tmp_path, key, value):
    p = profile(tmp_path)
    p[key] = value
    with pytest.raises(ValueError):
        authority.authorized(p)


def test_no_authority_for_other_stock_device(tmp_path):
    p = profile(tmp_path)
    p["device"] = "LivingRoomSocketTableRight"
    p["ieee"] = "0xa4c13852d7bbc587"
    with pytest.raises(ValueError, match="two exact"):
        authority.authorized(p)


def test_legacy_stock_conversion_still_has_off_proof(tmp_path):
    p = profile(tmp_path)
    p["stock_client_preserve_loaded"] = False
    assert authority.authorized(p) is False
    assert waiver.validate({}) is False
    site = {
        "publish": {"url_base": "http://example.invalid/bseed"},
        "serve_root": str(tmp_path), "broker": "127.0.0.1",
        "mqtt_config": str(tmp_path / "mqtt"), "network_lock_dir": str(tmp_path),
        "network_id": "0x00124b002d12b1fd",
        "pm_ssh_host": "127.0.0.1", "pm_ssh_key": "x",
    }
    live = dict(friendly_name="HALLSocketMain",
                ieee_address=authority.TARGETS["HALLSocketMain"],
                manufacturer="_TZ3000_b28wrpvx", model_id="TS011F",
                type="Router", software_build_id=None)
    route = migrate.choose_route(live, "client")
    old = migrate.build_profile(site, live, route,
        dict(build="1.2.5-bseedcli27", imageType=65024, manufacturerCode=4417),
        image=tmp_path / "old.ota", sha256=authority.TRANSPORT_SHA,
        native_image=tmp_path / "native.ota", native_sha256=authority.NATIVE_SHA,
        join_via="OtherRouter", workdir=tmp_path / "old", folder="old")
    assert old["auto_relay_off"] is True
    assert old["pm_preflash_load_proof"] == "physically_unloaded"


def test_loaded_canary_checks_without_commanding_relay(monkeypatch, tmp_path):
    p = profile(tmp_path)
    calls = []
    monkeypatch.setattr(campaign.subprocess, "call",
                        lambda arg: calls.append(arg) or 0)
    assert campaign.auto_relay_off_then_check(p) == 0
    assert len(calls) == 1
    assert "--mode" in calls[0] and "check" in calls[0]
    assert "--waive-preflash-pm" in calls[0]


def test_postflash_uses_custom_key_and_never_sets_relay(tmp_path):
    p = profile(tmp_path)
    class Reader:
        def __init__(self, state):
            self.calls = []
            self.state = state
        def read(self, key):
            self.calls.append(key)
            return self.state
        def set(self, *args, **kwargs):
            raise AssertionError("Never command a relay during loaded OTA")
    reader = Reader("ON")
    assert relay.verify_after_flash(p, reader=reader) == "ON"
    assert reader.calls == ["state_relay"]
    with pytest.raises(ValueError, match="Postflash relay changed"):
        relay.verify_after_flash(p, reader=Reader("OFF"))
