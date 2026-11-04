"""Terminalize ONE completed OTA transfer while leaving postflash recovery pending.

This is NOT application success, not a role verification, not a new flash permit,
and not a PM-quarantine release. Exact owner and evidence are mandatory.
"""
from __future__ import annotations
import argparse,datetime as dt,hashlib,json,os,tempfile,uuid
from pathlib import Path
import yaml,paho.mqtt.client as mqtt
import threading,time
from bseed_network_campaign_lock import (
    DEFERRED_RECOVERY_PHASE,read_lock,update,release_deferred_recovery,
)
from bseed_ota_campaign import load_profile,network_lock_path

CABINET = "0xa4c138c5f07ee732"
SHA = "69976cc008da64da88d0ec6c345b2a059aa5326e4c44de54842a98a5dd5d69f6"
START = "ota_transfer_ok_postflash_unverified"
FINAL = DEFERRED_RECOVERY_PHASE
SOURCE_VER = 302329874
TARGET_VER = 302329889

def parse_time(s):
    value=dt.datetime.fromisoformat(s)
    if value.tzinfo is None:raise ValueError("Evidence timestamp must include timezone")
    return value.astimezone(dt.timezone.utc)

def exact(profile):
    if (profile.get("device"),profile.get("ieee"),profile.get("sha256"),
        profile.get("preflash_role"),profile.get("postflash_role"),
        profile.get("preflash_build"),profile.get("postflash_build")) != (
        "WorkroomSocketCabinet",CABINET,SHA,"Router","EndDevice",
        "1.2.5-bseedv8u5-rc5","1.2.5-bseedcli22"):
        raise ValueError("Deferred recovery restricted to one exact Cabinet migration")
    if (profile.get("require_pm"),profile.get("pm_preflash_load_proof")) != (True,"physically_unloaded"):
        raise ValueError("Cabinet physical and PM class must remain pinned")

def validate(profile,work,network,live,rejoin,guard,status,*,now=None):
    exact(profile)
    now=now or dt.datetime.now(dt.timezone.utc)
    token=work.get("token")
    if (not token or
        (work.get("device"),work.get("ieee"),work.get("sha256"),work.get("phase")) !=
        (profile["device"],CABINET,SHA,START)):
        raise ValueError("Exact completed-transfer work authority required")
    if (network.get("token"),network.get("device"),network.get("ieee"),
        network.get("image_sha256"),network.get("phase"),network.get("network_id")) != (
        token,profile["device"],CABINET,SHA,START,profile["network_id"]):
        raise ValueError("Exact completed-transfer network authority required")
    response=work.get("response") or {}
    payload=response.get("data") or {}
    if (response.get("status"),response.get("transaction"),payload.get("id"),
        (payload.get("from") or {}).get("file_version"),
        (payload.get("to") or {}).get("file_version")) != (
        "ok",token,CABINET,SOURCE_VER,TARGET_VER):
        raise ValueError("Transport never completed with exact matching OTA token")
    completed=parse_time(work["completed"])
    if not dt.timedelta(minutes=20) <= now-completed <= dt.timedelta(days=7):
        raise ValueError("Completed transfer must have 20 minutes quiet time")
    if (status.get("phase"),(status.get("update") or {}).get("state"),
        (status.get("update") or {}).get("installed_version")) != (
        START,"idle",TARGET_VER):
        raise ValueError("OTA service has not reached completed idle state")
    if (status.get("response") or {}).get("transaction") != token:
        raise ValueError("OTA status response token differs")
    if (rejoin.get("ieee"),rejoin.get("target"),rejoin.get("result"),
        rejoin.get("identity_verified"),(rejoin.get("close_response") or {}).get("status"),
        rejoin.get("live_node_descriptor")) != (
        CABINET,profile["device"],"unconfirmed",False,"ok",None):
        raise ValueError("Scoped rejoin must have failed and closed its join window")
    if parse_time(rejoin["observed_at"]) < completed:
        raise ValueError("Rejoin observation predates finished transfer")
    if (guard.get("ieee"),guard.get("sha256"),guard.get("token"),
        guard.get("phase"),guard.get("expected_role")) != (
        CABINET,SHA,token,"enabled","EndDevice"):
        raise ValueError("Unresolved PM telemetry quarantine must be preserved")
    inventory=live.get("inventory") or {}
    if (live.get("bridge_state"),live.get("permit_join"),
        inventory.get("ieee_address"),inventory.get("friendly_name"),
        inventory.get("interview_state"),inventory.get("software_build_id")) != (
        "online",False,CABINET,profile["device"],"FAILED","1.2.5-bseedcli8"):
        raise ValueError("Live bridge must be online and Cabinet interview still failed")
    if live.get("device_ota_active") is True or live.get("running_ota_runners") is not False:
        raise ValueError("No active OTA runner/transaction allowed")
    # The cached Router/cli8 identity is NOT proof that the old image is running.
    return dict(result=FINAL,device=profile["device"],ieee=CABINET,token=token,
        image_sha256=SHA,transport_completed=True,installed_image_verified=False,
        current_zigbee_role_verified=False,hardware_acceptance=False,
        pm_telemetry_quarantine_retained=True,old_campaign_reusable=False,
        new_ota_to_same_ieee_authorized=False,power_cycle_deferred=True,
        pending="operator power cycle, fresh ZDO/Basic, PM rejoin and provisioning",
        transfer_completed_at=work["completed"])

def retained_bridge_snapshot(profile,timeout=9):
    config=yaml.safe_load(Path(profile["mqtt_config"]).read_text(encoding="utf8"))["mqtt"]
    base=config.get("base_topic","zigbee2mqtt")
    state={};connected=threading.Event()
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="bseed-cabinet-deferral-"+uuid.uuid4().hex)
    c.username_pw_set(config.get("user",""),config.get("password",""))
    def onconnect(cl,u,f,rc,p):
        if rc.is_failure:return
        for name in ("bridge/state","bridge/info","bridge/devices","WorkroomSocketCabinet/availability"):
            cl.subscribe(base+"/"+name)
        connected.set()
    def onmessage(cl,u,message):
        try:state[message.topic.removeprefix(base+"/")]=json.loads(message.payload)
        except (UnicodeDecodeError,ValueError):pass
    c.on_connect=onconnect;c.on_message=onmessage
    c.connect(profile["broker"],1883,10);c.loop_start()
    try:
        if not connected.wait(timeout):raise TimeoutError("MQTT bridge unavailable")
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and not all(k in state for k in ("bridge/state","bridge/info","bridge/devices")):
            time.sleep(.1)
    finally:c.loop_stop();c.disconnect()
    info=state.get("bridge/info") or {}
    devs=state.get("bridge/devices") or []
    matches=[d for d in devs if d.get("ieee_address")==CABINET]
    if len(matches)!=1:raise ValueError("Cabinet missing/duplicated from MQTT inventory")
    bridge=(state.get("bridge/state") or {}).get("state")
    avail=state.get("WorkroomSocketCabinet/availability")
    if avail is not None and avail.get("state")!="offline":
        raise ValueError("Cabinet is no longer offline; reconcile role instead")
    return dict(bridge_state=bridge,permit_join=info.get("permit_join"),inventory=matches[0],
        availability=avail,device_ota_active=False,running_ota_runners=False)

def atomic_json(path,data):
    fd,tmp=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf8") as f:
            json.dump(data,f,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def terminalize(profile):
    import psutil
    running=[]
    for p in psutil.process_iter(["cmdline"]):
        args=p.info.get("cmdline") or []
        if any(str(x).endswith(("bseed_targeted_z2m_ota.py","bseed_ota_campaign.py")) for x in args):
            running.append(p.pid)
    if running:raise ValueError("Existing OTA runners still active: "+repr(running))
    workdir=Path(profile["workdir"]);work_path=workdir/"ACTIVE_LOCK.json"
    work=json.loads(work_path.read_text(encoding="utf8"))
    net_path=network_lock_path(profile,required=True)
    network=read_lock(net_path)
    if network is None:raise ValueError("Network lock unexpectedly missing")
    path_rejoin=next(iter(workdir.glob("rejoin_*.json")),None)
    if path_rejoin is None or len(list(workdir.glob("rejoin_*.json")))!=1:
        raise ValueError("Exactly one failed rejoin evidence required")
    paths=[path_rejoin,workdir/"PM_TELEMETRY_GUARD.json",workdir/"LIVE_STATUS.json"]
    rejoin,guard,status=[json.loads(p.read_text(encoding="utf8")) for p in paths]
    live=retained_bridge_snapshot(profile)
    live["running_ota_runners"]=not not running
    facts=validate(profile,work,network,live,rejoin,guard,status)
    ev=workdir/"DEFERRED_RECOVERY_NETWORK_RELEASE.json"
    if ev.exists():raise ValueError("Deferred campaign already terminalized")
    record=dict(facts,at=dt.datetime.now(dt.timezone.utc).isoformat(),
        network_lease_released=False,operator_intent="defer power cycle and continue other sockets",
        live_bridge=live,evidence_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        work_lock_sha256=hashlib.sha256(work_path.read_bytes()).hexdigest())
    with ev.open("x",encoding="utf8") as f:json.dump(record,f,indent=2);f.write("\n")
    work.update(phase=FINAL,deferred_recovery_evidence=str(ev),
                hardware_acceptance=False,current_zigbee_role_verified=False,
                new_ota_to_same_ieee_authorized=False)
    atomic_json(work_path,work)
    update(net_path,facts["token"],FINAL,deferred_recovery_evidence=str(ev),
        hardware_acceptance=False,current_zigbee_role_verified=False)
    release_deferred_recovery(net_path,facts["token"])
    record["network_lease_released"]=True
    atomic_json(ev,record)
    print("CABINET_DEFERRED_UNVERIFIED_NETWORK_RELEASED",str(ev),flush=True)
    return record

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--profile",required=True)
    a.add_argument("--confirm-ieee",required=True)
    a.add_argument("--confirm-sha256",required=True)
    args=a.parse_args()
    p=load_profile(args.profile);exact(p)
    if args.confirm_ieee!=CABINET or args.confirm_sha256!=SHA:
        raise ValueError("Explicit exact target and SHA required")
    terminalize(p)

if __name__=="__main__":main()
