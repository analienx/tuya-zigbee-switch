"""Software-verified OTA lease closure: transport and hardware acceptance stay distinct."""
import copy
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
from bseed_postflash_software_close import validate


def verified_set():
    profile = {
        "device": "KitchenSocketRight", "ieee": "0xabcdef",
        "sha256": "a" * 64, "network_id": "coord-01",
        "file_version": "0x12053021",
        "preflash_build": "1.2.5-bseedr17",
        "postflash_build": "1.2.5-bseedr19",
        "preflash_role": "Router", "postflash_role": "Router",
        "require_pm": True,
    }
    token = "txn-abc"
    work = {
        "phase": "postflash_candidate", "token": token,
        "device": profile["device"], "ieee": profile["ieee"], "sha256": profile["sha256"],
        "started": "2026-11-03T17:35:00+01:00",
        "completed": "2026-11-03T19:20:00+01:00",
        "preflash_state": {"state_relay": "ON", "energy": 10.93},
        "response": {
            "status": "ok", "transaction": token,
            "data": {"id": profile["ieee"],
                     "to": {"software_build_id": profile["postflash_build"],
                            "file_version": 0x12053021}},
        },
    }
    net = {
        "phase": "postflash_candidate", "token": token, "device": profile["device"],
        "ieee": profile["ieee"], "image_sha256": profile["sha256"], "network_id": "coord-01",
    }
    post = {
        "observed_at": "2026-11-03T21:41:00+01:00",
        "target_name": profile["device"], "target_ieee": profile["ieee"],
        "expected_role": "Router", "expected_build": profile["postflash_build"],
        "result": "postflash_candidate", "issues": [], "errors": [], "bridge": "online",
        "fresh_mqtt_state_observed": True, "zdo_error": None,
        "cached_inventory_role_disagrees_with_live_zdo": False,
        "live_node_descriptor": {"role": "Router"},
        "inventory": {"ieee_address": profile["ieee"],
                      "software_build_id": profile["postflash_build"],
                      "type": "Router", "interview_completed": True},
        "state": {"state_relay": "ON", "energy": 11.17,
                  "device": {"ieeeAddr": profile["ieee"],
                             "softwareBuildID": profile["postflash_build"],
                             "type": "Router"}},
        "same_role_retention": {"issues": [], "physical_acceptance": False},
        "pm_readiness": {"issues": []},
    }
    settings = {"2/genOnOff": {"startUpOnOff": 255}}
    base = {"ieee": profile["ieee"], "role": "Router", "settings_snapshot": settings}
    before = {"at": "2026-11-01T09:41:46+01:00",
              "device": profile["device"], "ieee": profile["ieee"],
              "expected_role": "Router", "expected_build": profile["preflash_build"],
              "result": "read_only_audit_candidate",
              "settings_snapshot": settings, "issues": [], "writes": 0,
              "missing_reporting": []}
    after = {"at": "2026-11-03T20:22:08+01:00",
             "device": profile["device"], "ieee": profile["ieee"],
             "expected_role": "Router", "expected_build": profile["postflash_build"],
             "result": "read_only_audit_candidate",
             "settings_snapshot": settings, "settings_drift": {},
             "issues": [], "writes": 0, "missing_reporting": [],
             "mqtt": {"messages": 5, "span_seconds": 50,
                      "latest_metrics": {"energy": 11.17, "voltage": 246.9}}}
    return profile, work, net, post, after, before, base


NOW = dt.datetime.fromisoformat("2026-11-03T21:42:00+01:00")


def run(parts):
    return validate(*parts, now=NOW)


def test_strict_success_releases_network_but_never_hardware_accepts():
    result = run(verified_set())
    assert result["otaTransportSuccess"] is True
    assert result["softwareVerification"] is True
    assert result["hardwareAcceptance"] is False
    assert result["physicalAcceptance"] is False


@pytest.mark.parametrize("field,bad", [
    ("phase", "ota_transfer_ok_postflash_unverified"),
    ("token", "foreign-campaign"),
    ("sha256", "0"*64),
])
def test_wrong_work_campaign_refused(field, bad):
    case = list(copy.deepcopy(verified_set()))
    case[1][field] = bad
    with pytest.raises(ValueError):
        run(case)


def test_fail_closed_on_unproven_transfer():
    for update in ({"status": "error"}, {"transaction": "old-txn"},
                   {"data": {"id": "wrong-target"}}, {"data": {"to": {"software_build_id": "r18"}}}):
        case = list(copy.deepcopy(verified_set()))
        case[1]["response"].update(update)
        with pytest.raises(ValueError, match="transport"):
            run(case)


@pytest.mark.parametrize("modify", [
    lambda c: c[3].update(fresh_mqtt_state_observed=False),
    lambda c: c[3].update(zdo_error="no_response"),
    lambda c: c[3]["inventory"].update(type="EndDevice"),
    lambda c: c[3]["state"].update(state_relay="OFF"),
    lambda c: c[3].update(observed_at="2026-11-03T19:21:00+01:00"),
])
def test_postflash_missing_or_wrong_target_data_refused(modify):
    case = list(copy.deepcopy(verified_set()))
    modify(case)
    # A valid independent sample is required. Some modifications might still
    # pass chronology; the relay/role/freshness predicates catch the others.
    if case[3]["observed_at"] == "2026-11-03T19:21:00+01:00":
        case[3]["observed_at"] = "2026-11-02T19:21:00+01:00"
    with pytest.raises(ValueError):
        run(case)


@pytest.mark.parametrize("modify", [
    lambda c: c[4].update(issues=["bad"]),
    lambda c: c[4].update(writes=1),
    lambda c: c[4].update(missing_reporting=["rmsVoltage"]),
    lambda c: c[4].update(settings_drift={"startUpOnOff": {"previous": 0, "current": 255}}),
    lambda c: c[4]["settings_snapshot"]["2/genOnOff"].update(startUpOnOff=0),
    lambda c: c[4]["mqtt"]["latest_metrics"].update(energy=0),
    lambda c: c[5].update(expected_build="older"),
    lambda c: c[6].update(ieee="foreign"),
])
def test_pm_acceptance_evidence_gaps_refused(modify):
    case = list(copy.deepcopy(verified_set()))
    modify(case)
    with pytest.raises(ValueError):
        run(case)


def test_stale_and_future_evidence_refused():
    case = list(copy.deepcopy(verified_set()))
    case[3]["observed_at"] = "2026-11-03T20:20:00+01:00"
    with pytest.raises(ValueError, match="too old"):
        run(case)
    case[3]["observed_at"] = "2026-11-04T21:41:00+01:00"
    with pytest.raises(ValueError):
        run(case)
