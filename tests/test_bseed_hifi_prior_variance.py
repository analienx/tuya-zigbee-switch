"""Exact operator-authorized source-only continuation; negative acceptance persists."""
import datetime as dt

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
import bseed_kitchen_prior_variance as variance


def evidence(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    ieee, role, build, phase, token, sha = variance.PREVIOUS["LivingRoomSocketHifiLeft"]
    close = {
        "result": phase, "network_lease_released": True,
        "new_ota_same_ieee_authorized": False,
        "operator_operational_confirmation": True,
        "fleet_release_approved": False,
        "preserved_postflash_issues": ["Relay state changed across same-role OTA"],
        "device": "LivingRoomSocketHifiLeft", "ieee": ieee,
        "transaction": token, "image_sha256": sha,
        "installed_build": build, "full_physical_acceptance": False,
    }
    location = tmp_path / "close.json"
    import json
    location.write_text(json.dumps(close), encoding="utf8")
    rec = {
        "device": "LivingRoomSocketHifiLeft", "ieee": ieee, "phase": phase,
        "token": token, "sha256": sha, "hardware_acceptance": False,
        "new_ota_same_ieee_authorized": False,
        "response": {"status": "ok", "transaction": token,
            "data": {"id": ieee, "to": {"software_build_id": build}}},
        "operator_close_evidence": str(location),
        "network_lock": str(tmp_path / "released-original-network.json"),
        "completed": (now - dt.timedelta(minutes=9)).isoformat(),
    }
    live = {"ieeeAddr": ieee, "swBuildId": build, "type": role,
        "interviewCompleted": True, "manufName": "b28wrpvx",
        "modelId": "TS011F-BS-PM", "lastSeen": int(now.timestamp()*1000)}
    return rec, live, close, location


def test_hifi_exact_old_variance_authorizes_only_new_source_ota(tmp_path, monkeypatch):
    monkeypatch.setattr(variance, "read_lock", lambda _: None)
    rec, live, close, location = evidence(tmp_path)
    assert variance.validated("LivingRoomSocketHifiLeft", rec, live, enabled=True)
    assert not variance.validated("LivingRoomSocketHifiLeft", rec, live, enabled=False)
    assert not variance.validated("LivingRoomSocketHifiLeft", dict(rec, token="wrong"), live, enabled=True)
    assert not variance.validated("LivingRoomSocketHifiLeft", dict(rec, hardware_acceptance=True), live, enabled=True)
    assert not variance.validated("LivingRoomSocketHifiLeft", rec, dict(live, swBuildId="1.2.5-bseedcli27"), enabled=True)
    assert not variance.validated("LivingRoomSocketHifiLeft", rec, dict(live, lastSeen=1), enabled=True)
    assert not variance.validated("KitchenSocketLeft", rec, live, enabled=True)
    import json
    for key, value in [
        ("operator_operational_confirmation", False),
        ("fleet_release_approved", True),
        ("preserved_postflash_issues", []),
        ("full_physical_acceptance", True),
    ]:
        invalid = dict(close, **{key: value})
        location.write_text(json.dumps(invalid), encoding="utf8")
        assert not variance.validated("LivingRoomSocketHifiLeft", rec, live, enabled=True), key
    location.write_text(json.dumps(close), encoding="utf8")
    monkeypatch.setattr(variance, "read_lock", lambda _: {"token": rec["token"]})
    assert not variance.validated("LivingRoomSocketHifiLeft", rec, live, enabled=True)
