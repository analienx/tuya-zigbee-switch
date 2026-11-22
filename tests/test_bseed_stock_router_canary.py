"""Exact-stock Router candidate gates: no relay writes, no Client payload."""
from pathlib import Path
import json
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper_scripts"))
import bseed_socket_migrate as mig
import bseed_ota_campaign as campaign
from bseed_source_build import source_build_matches
from bseed_targeted_z2m_ota import validate_stock_router_no_load
from bseed_pm_telemetry_guard import begin, is_stock_guard, guard_path

DEVICE = "WRSocketWindowRightL"
IEEE = "0xa4c138793505c5cc"


def live():
    return dict(friendly_name=DEVICE, ieee_address=IEEE,
                manufacturer="_TZ3000_b28wrpvx", model_id="TS011F",
                type="Router", software_build_id=None)


def profile(tmp_path):
    route = mig.choose_route(live(), "router")
    assert (route["kind"], route["variant"], route["wrapper_type"]) == (
        "stock_pm_to_router", "pm-router", 54179)
    site = dict(publish=dict(url_base="https://invalid.example/router"),
                serve_root=str(tmp_path/"serve"),
                mqtt_config=str(tmp_path/"mqtt.yaml"), broker="invalid",
                network_lock_dir=str(tmp_path/"locks"),
                network_id="0x00124b0000000000",
                pm_ssh_host="invalid", pm_ssh_key=str(tmp_path/"key"))
    candidate = dict(build="1.2.5-bseedr24", manufacturerCode=4417,
                     imageType=43556, fileVersion=302329894)
    p = mig.build_profile(site, live(), route, candidate,
        image=tmp_path/"serve"/"stock.ota", sha256="a"*64,
        native_image=tmp_path/"native.ota", native_sha256="b"*64,
        workdir=tmp_path/"work", folder="stock-test")
    path = tmp_path/"work"/"profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(p), encoding="utf8")
    return campaign.load_profile(path)


def test_conversion_is_explicit_router_only():
    assert mig.choose_route(live(), "router")["kind"] == "stock_pm_to_router"
    assert mig.choose_route(live(), "client")["kind"] == "stock_pm_to_client"
    with pytest.raises(mig.Refused, match="explicit"):
        mig.choose_route(live(), "same")


def test_router_profile_is_exact_and_does_not_toggle_relay(tmp_path):
    p = profile(tmp_path)
    assert campaign.stock_router_conversion(p) is True
    assert p["preflash_role"] == p["postflash_role"] == "Router"
    assert p["auto_relay_off"] is False and p["relay_state_policy"] == "preserve"
    assert p["expect_relay"] == "OFF" and p["relay_get_key"] == "state"
    assert p["stock_router_conversion"] is True and not p.get("preflash_build")
    assert p["postflash_relay_get_key"] == "state_relay"
    assert source_build_matches(p, None)
    assert not source_build_matches(p, "1.2.5-bseedcli27")
    assert p["pm_preflash_load_proof"] == "meter" and p["max_reported_watts"] == 0.5


@pytest.mark.parametrize("key,value", [
    ("ieee", "0xa4c1380000000000"), ("device", "Other"),
    ("postflash_role", "EndDevice"), ("expect_relay", "ON"),
    ("auto_relay_off", True), ("relay_state_policy", "off"),
    ("stock_router_conversion", None), ("native_image", None),
    ("postflash_build", "1.2.5-bseedcli27"),
    ("max_reported_watts", 10.0), ("pm_preflash_load_proof", "physically_unloaded"),
])
def test_tampered_stock_router_profile_refused(tmp_path, key, value):
    p = profile(tmp_path)
    p[key] = value
    with pytest.raises(ValueError):
        campaign.stock_router_conversion(p)


def fresh_relay(p):
    return dict(device=dict(friendlyName=p["device"], ieeeAddr=p["ieee"],
                            manufacturerName="_TZ3000_b28wrpvx", type="Router"),
                state="OFF", power=0, current=0)


def test_reported_zero_not_falsely_fresh_meter(tmp_path):
    p = profile(tmp_path)
    assert validate_stock_router_no_load(fresh_relay(p), p)["metering_stamp_verified"] is False


@pytest.mark.parametrize("field,value", [
    ("state", "ON"), ("power", 1), ("current", 0.1),
    ("power", None), ("current", None),
    ("power", float("nan")), ("power", False),
])
def test_relay_and_zero_load_fail_closed(tmp_path, field, value):
    p = profile(tmp_path)
    state = fresh_relay(p)
    state[field] = value
    with pytest.raises(AssertionError):
        validate_stock_router_no_load(state, p)


@pytest.mark.parametrize("field,value", [
    ("ieeeAddr", "0x1111222233334444"), ("type", "EndDevice"),
    ("manufacturerName", "b28wrpvx"), ("friendlyName", "Other"),
])
def test_stock_identity_fail_closed(tmp_path, field, value):
    p = profile(tmp_path)
    state = fresh_relay(p)
    state["device"][field] = value
    with pytest.raises(AssertionError):
        validate_stock_router_no_load(state, p)


def test_stock_pm_guard_does_not_claim_quarantine_or_provision(tmp_path):
    p = profile(tmp_path)
    begin(p, "bseed-ota-test")
    guard = json.loads(guard_path(p).read_text(encoding="utf8"))
    assert guard["quarantine_enabled"] is False
    assert guard["requires_pm_provision"] is False
    assert is_stock_guard(p, guard)
