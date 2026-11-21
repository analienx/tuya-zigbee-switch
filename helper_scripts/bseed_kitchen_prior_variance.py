"""Exact prior-OTA continuation approved 2026-11-21; old negative findings remain."""
import datetime as dt
import json
from pathlib import Path
from bseed_network_campaign_lock import read_lock

PREVIOUS = {
 "KitchenSocketLeft": (
  "0xa4c138241e3de538","EndDevice","1.2.5-bseedcli25",
  "postflash_operator_confirmed_variance_network_released",
  "bseed-ota-1123b86ee4e8491ab948efe99bb3af63",
  "4246f097a8927fcfa9a02d16d7ee966cc96ea14487681b2cd9f34b50b41dc914",
 ),
 "KitchenSocketRight": (
  "0xa4c138075cd16ed4","Router","1.2.5-bseedr22",
  "postflash_operator_override_settings_drift_network_released",
  "bseed-ota-420a4ae61b7f46fcb5d7b50cd1e5ce53",
  "0e26b616c1f5a48cb4ee1506d1972ea93ffde3857024d9fdb64f4544734a4010",
 ),
}

def validated(device, record, live, *, enabled):
    if enabled is not True or device not in PREVIOUS:
        return False
    ieee, role, build, phase, token, sha = PREVIOUS[device]
    if (record.get("device"),record.get("ieee"),record.get("phase"),record.get("token"),
        record.get("sha256"),record.get("hardware_acceptance"),
        record.get("new_ota_same_ieee_authorized")) != (
        device,ieee,phase,token,sha,False,False):
        return False
    if (live.get("ieeeAddr"),live.get("swBuildId"),live.get("type"),
        live.get("interviewCompleted"),live.get("manufName"),
        live.get("modelId")) != (
        ieee,build,role,True,"b28wrpvx","TS011F-BS-PM"):
        return False
    reply=record.get("response") or {}
    data=reply.get("data") or {}
    if (reply.get("status"),reply.get("transaction"),data.get("id"),
       (data.get("to") or {}).get("software_build_id")) != ("ok",token,ieee,build):
        return False
    evidence=Path(record.get("operator_close_evidence") or "")
    if not evidence.is_file():
        return False
    close=json.loads(evidence.read_text(encoding="utf8"))
    if (close.get("result"),close.get("network_lease_released"),
        close.get("new_ota_same_ieee_authorized")) != (phase,True,False):
        return False
    # Evidence has two historical formats; both must pin the old image/token.
    if device=="KitchenSocketLeft":
        if (close.get("device"),close.get("ieee"),close.get("transaction"),
            close.get("image_sha256"),close.get("installed_build"),
            close.get("full_physical_acceptance")) != (
            device,ieee,token,sha,build,False):
            return False
    else:
        if (close.get("device"),close.get("ieee"),close.get("token"),
            close.get("ota_sha256"),close.get("installed_build"),
            close.get("hardware_acceptance")) != (
            device,ieee,token,sha,build,False):
            return False
    now=dt.datetime.now(dt.timezone.utc)
    completed=dt.datetime.fromisoformat(record["completed"])
    if completed.tzinfo is None:return False
    seen=dt.datetime.fromtimestamp(live.get("lastSeen",0)/1000,dt.timezone.utc)
    if not (completed<=seen<=now+dt.timedelta(seconds=30) and
            now-seen<=dt.timedelta(minutes=15)):
        return False
    # Old shared owner must have been released; active OTHER campaign is fine.
    net=read_lock(record["network_lock"])
    if net and net.get("token")==token:
        return False
    return True
