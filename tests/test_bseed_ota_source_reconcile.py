"""Direct fast-reconciliation regression tests, including the crash-window invariant."""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_ota_source_reconcile as sr
import bseed_ota_resume_supervisor as sup
import bseed_targeted_z2m_ota as runner
from bseed_network_campaign_lock import acquire, read_lock


IEEE = "0xa4c138da1333dc70"
DEVICE = "LivingRoomSocketHifiLeft"
SHA = "a" * 64
TOKEN = "bseed-ota-original"


def fixture(tmp_path, monkeypatch, fresh_build="1.2.5-bseedcli6", fresh_role="EndDevice"):
    work = tmp_path / "run"
    work.mkdir()
    broker = tmp_path / "mqtt.yaml"
    broker.write_text("mqtt:\n  user: test\n  password: test\n  base_topic: zigbee2mqtt\n")
    profile = {
        "workdir": str(work), "mqtt_config": str(broker), "broker": "localhost",
        "device": DEVICE, "ieee": IEEE, "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM", "preflash_role": "EndDevice",
        "preflash_build": "1.2.5-bseedcli6", "postflash_build": "1.2.5-bseedcli14",
        "postflash_role": "EndDevice", "sha256": SHA,
        "url": "http://example.test/image.ota", "index_url": "http://example.test/index.json",
        "relay_get_key": "state_relay", "network_id": "0x0011223344556677",
        "require_pm": False,
    }
    work_lock = {"device": DEVICE, "ieee": IEEE, "sha256": SHA,
                 "phase": "update_error", "token": TOKEN}
    (work / "ACTIVE_LOCK.json").write_text(json.dumps(work_lock))
    (work / "LAST_CHECK.json").write_text(json.dumps({"device":DEVICE, "timestamp":time.time(),
                                                      "response":{"status":"ok"}}))
    net = tmp_path / "network-lock.json"
    acquire(net, network_id=profile["network_id"], token=TOKEN,
            device=DEVICE, ieee=IEEE, image_sha256=SHA)
    # Simulate previous OTA failure without mutating the actual network.
    from bseed_network_campaign_lock import update
    update(net,TOKEN,"update_error")
    monkeypatch.setattr(sr, "load_profile", lambda _: dict(profile))
    monkeypatch.setattr(sr, "network_lock_path", lambda _p, required=False: net)

    inventory = [{
        "ieee_address": IEEE, "friendly_name": DEVICE,
        "manufacturer": "b28wrpvx", "model_id": "TS011F-BS-PM",
        "type": "EndDevice", "software_build_id": "1.2.5-bseedcli6",
        "interview_state": "SUCCESSFUL",
    }]

    class Client:
        def __init__(self, *args, **kwargs):
            self.on_connect = None
            self.on_message = None
        def username_pw_set(self,*args):pass
        def connect(self,*args):pass
        def subscribe(self,*args):pass
        def loop_stop(self):pass
        def disconnect(self):pass
        def emit(self,tail,payload,retained=True):
            msg=SimpleNamespace(topic="zigbee2mqtt/"+tail,
                payload=json.dumps(payload).encode(),retain=retained)
            self.on_message(self,None,msg)
        def loop_start(self):
            self.on_connect(self,None,None,SimpleNamespace(is_failure=False),None)
            self.emit("bridge/devices",inventory)
            self.emit("bridge/info",{"coordinator":{"ieee_address":profile["network_id"]}})
            self.emit("bridge/state",{"state":"online"})
        def publish(self,topic,payload,qos=1):
            if topic.endswith("/get"):
                self.emit(DEVICE,{
                    "state_relay":"OFF",
                    "device":{"ieeeAddr":IEEE,"softwareBuildID":fresh_build,"type":fresh_role},
                },retained=False)
            elif topic.endswith("/device/ota_update/check"):
                tx=json.loads(payload)["transaction"]
                self.emit("bridge/response/device/ota_update/check",{
                    "transaction":tx,"status":"ok","data":{
                        "id":IEEE,"update_available":True,"source":profile["url"]}},retained=False)
            return SimpleNamespace(wait_for_publish=lambda _s:None)
    monkeypatch.setattr(sr.mqtt, "Client", Client)
    return profile,work,net


def test_fast_reconcile_invalidates_check_and_holds_network_lock(tmp_path, monkeypatch):
    profile,work,net=fixture(tmp_path,monkeypatch)
    result=sr.reconcile(tmp_path/"profile.json",IEEE,observe_seconds=0,verify_candidate=False)
    assert result["result"] == sup.PENDING_PHASE
    assert result["fresh_get_build_role_verified"] is True
    assert not (work/"LAST_CHECK.json").exists()
    assert list(work.glob("CHECK_ARCHIVE_*.json"))
    assert json.loads((work/"ACTIVE_LOCK.json").read_text())["phase"] == sup.PENDING_PHASE
    assert read_lock(net)["phase"] == sup.PENDING_PHASE
    assert runner.new_campaign_allowed(json.loads((work/"ACTIVE_LOCK.json").read_text())) is False
    # Simulate a crash now: old pre-failure check is gone, network remains owned.


@pytest.mark.parametrize("build,role",[
    ("1.2.5-bseedcli14","EndDevice"),
    ("1.2.5-bseedcli6","Router"),
    (None,"EndDevice"),
])
def test_fast_reconcile_rejects_missing_or_different_live_source(tmp_path,monkeypatch,build,role):
    profile,work,net=fixture(tmp_path,monkeypatch,fresh_build=build,fresh_role=role)
    with pytest.raises(ValueError,match="Fresh source identity"):
        sr.reconcile(tmp_path/"profile.json",IEEE,observe_seconds=0,verify_candidate=False)
    assert read_lock(net)["phase"] == "update_error"
    assert (work/"LAST_CHECK.json").exists()


def test_candidate_finalization_requires_fresh_exact_check(tmp_path,monkeypatch):
    profile,work,net=fixture(tmp_path,monkeypatch)
    sr.reconcile(tmp_path/"profile.json",IEEE,observe_seconds=0,verify_candidate=False)
    monkeypatch.setattr(sup.campaign, "network_lock_path", lambda _p,required=False: net)
    source=json.loads((work/"ACTIVE_LOCK.json").read_text())["reconciliation_evidence"]
    source_at=dt.datetime.fromisoformat(json.loads(Path(source).read_text())["at"]).timestamp()
    check={"device":DEVICE,"ieee":IEEE,"sha256":SHA,"timestamp":time.time(),
           "transaction":"bseed-ota-fresh",
           "response":{"transaction":"bseed-ota-fresh","status":"ok","data":{
               "id":IEEE,"source":profile["url"],"update_available":True}}}
    (work/"LAST_CHECK.json").write_text(json.dumps(check))
    evidence=sup.finalize_deferred_reconcile(profile,work)
    assert evidence.exists()
    assert not net.exists()
    accepted_lock = json.loads((work/"ACTIVE_LOCK.json").read_text())
    assert accepted_lock["phase"] == sup.READY_PHASE
    assert dt.datetime.fromisoformat(accepted_lock["reconciled_at"]).timestamp() < check["timestamp"]


@pytest.mark.parametrize("field,value",[
    ("timestamp",0),("transaction",""),("response_id","0x0000000000000001"),
])
def test_bad_deferred_check_cannot_release_network_lock(tmp_path,monkeypatch,field,value):
    profile,work,net=fixture(tmp_path,monkeypatch)
    sr.reconcile(tmp_path/"profile.json",IEEE,observe_seconds=0,verify_candidate=False)
    monkeypatch.setattr(sup.campaign, "network_lock_path", lambda _p,required=False: net)
    check={"device":DEVICE,"ieee":IEEE,"sha256":SHA,"timestamp":time.time(),
           "transaction":"bseed-ota-fresh",
           "response":{"transaction":"bseed-ota-fresh","status":"ok","data":{
               "id":IEEE,"source":profile["url"],"update_available":True}}}
    if field=="response_id":check["response"]["data"]["id"]=value
    else:check[field]=value
    (work/"LAST_CHECK.json").write_text(json.dumps(check))
    with pytest.raises(RuntimeError):
        sup.finalize_deferred_reconcile(profile,work)
    assert read_lock(net)["phase"] == sup.PENDING_PHASE


def test_cached_pm_power_cannot_pass_retry_gate():
    request=1_000_000
    assert runner.independently_fresh_pm_sample({
        "power":0,"bseed_pm_sample_power_w":0,
        "bseed_pm_sample_time_ms":request-1000,
    },baseline_ms=request-1000,request_ms=request,received_ms=request+200) is None
    assert runner.independently_fresh_pm_sample({
        "power":0,
    },baseline_ms=0,request_ms=request,received_ms=request+200) is None
    live=runner.independently_fresh_pm_sample({
        "power":0,"bseed_pm_sample_power_w":1.5,
        "bseed_pm_sample_time_ms":request+120,
    },baseline_ms=request-1000,request_ms=request,received_ms=request+200)
    assert live["power"] == 1.5
    assert live["sample_time_ms"] == request+120
