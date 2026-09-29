"""Scoped source-link recovery for an exact BSEED OTA campaign.

This helper does not flash, reset, remove, re-interview, bind, or restart Zigbee.
It opens permit-join only through the campaign profile's declared join_via router,
polls the exact target with a read-only GET until a fresh response proves the link
is back, then closes permit-join unconditionally.

It is intentionally weaker than source reconciliation: success here only proves
that the exact IEEE is reachable again. bseed_ota_source_reconcile.py must still
prove exact source role/build, a quiet OTA window, and exact candidate availability.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import yaml

from bseed_ota_campaign import load_profile


ELIGIBLE_PHASES = {
    "ota_running",
    "update_error",
    "update_timeout_or_unconfirmed",
    "source_unchanged_reconciled",
}


def now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--confirm-ieee", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--seconds", type=int)
    p.add_argument("--probe-every-seconds", type=int, default=5)
    a = p.parse_args(argv)

    profile = load_profile(Path(a.profile).expanduser().resolve())
    if a.confirm_ieee != profile["ieee"]:
        raise ValueError("Exact target IEEE confirmation required")
    router_name = profile.get("join_via")
    if not router_name:
        raise ValueError("Profile has no join_via router")
    seconds = int(a.seconds if a.seconds is not None else profile.get("join_seconds", 120))
    if not 30 <= seconds <= 180:
        raise ValueError("Scoped join window must be 30..180 seconds")
    if not 2 <= a.probe_every_seconds <= 30:
        raise ValueError("Probe cadence must be 2..30 seconds")

    work = Path(profile["workdir"])
    lock_path = work / "ACTIVE_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf8"))
    if (lock.get("device"), lock.get("ieee"), lock.get("sha256")) != (
        profile["device"], profile["ieee"], profile["sha256"]
    ):
        raise ValueError("Campaign lock identity/hash mismatch")
    if lock.get("phase") not in ELIGIBLE_PHASES:
        raise ValueError("Campaign phase is not eligible for scoped source-link recovery")

    output = Path(a.output).expanduser().resolve()
    if output.exists():
        raise ValueError("Output evidence must be new")

    mqtt_cfg = yaml.safe_load(Path(profile["mqtt_config"]).read_text(encoding="utf8"))["mqtt"]
    base = mqtt_cfg.get("base_topic", "zigbee2mqtt")
    token = "bseed-source-rejoin-" + uuid.uuid4().hex
    ready = threading.Event()
    wake = threading.Event()
    fresh = threading.Event()
    state = {
        "info": None,
        "devices": None,
        "permit_responses": {},
        "target": None,
        "events": [],
        "opened": False,
    }

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=token)
    client.username_pw_set(mqtt_cfg.get("user", ""), mqtt_cfg.get("password", ""))

    def on_connect(c, _u, _f, reason, _props):
        if reason.is_failure:
            return
        c.subscribe([
            (base + "/bridge/info", 1),
            (base + "/bridge/devices", 1),
            (base + "/bridge/response/permit_join", 1),
            (base + "/bridge/logging", 0),
            (base + "/" + profile["device"], 1),
        ])
        ready.set()

    def on_message(_c, _u, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        topic = message.topic
        if topic == base + "/bridge/info" and isinstance(data, dict):
            state["info"] = data
        elif topic == base + "/bridge/devices" and isinstance(data, list):
            state["devices"] = data
        elif topic == base + "/bridge/response/permit_join" and isinstance(data, dict):
            tx = data.get("transaction")
            if tx:
                state["permit_responses"][tx] = data
        elif topic == base + "/" + profile["device"] and isinstance(data, dict) and not message.retain:
            nested = data.get("device") or {}
            if nested.get("ieeeAddr") == profile["ieee"]:
                state["target"] = data
                fresh.set()
        elif topic == base + "/bridge/logging" and isinstance(data, dict):
            text = str(data.get("message", ""))
            if profile["ieee"] in text or profile["device"] in text:
                if len(state["events"]) < 100:
                    state["events"].append({"at": now(), "message": text[:400]})
        wake.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(profile["broker"], 1883, 10)
    client.loop_start()

    open_response = None
    close_response = None
    result = "unconfirmed"
    probes = 0
    try:
        if not ready.wait(10):
            raise TimeoutError("MQTT subscription not ready")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and (
            state["info"] is None or state["devices"] is None
        ):
            wake.wait(0.3)
            wake.clear()

        info = state["info"]
        devices = state["devices"]
        if not isinstance(info, dict) or not isinstance(devices, list):
            raise RuntimeError("Missing bridge info or inventory")
        if info.get("permit_join") is not False:
            raise ValueError("Joining already open; do not override another campaign")

        target = [d for d in devices if d.get("ieee_address") == profile["ieee"]]
        router = [d for d in devices if d.get("friendly_name") == router_name]
        if len(target) != 1 or len(router) != 1:
            raise ValueError("Exact target or join_via router missing/ambiguous")
        t = target[0]
        r = router[0]
        if (
            t.get("friendly_name"),
            t.get("manufacturer"),
            t.get("model_id"),
        ) != (
            profile["device"],
            profile["manufacturer"],
            profile["model"],
        ):
            raise ValueError("Cached target identity mismatch")
        if r.get("type") != "Router" or not (
            r.get("interview_completed") is True or r.get("interview_state") == "SUCCESSFUL"
        ):
            raise ValueError("join_via is not a verified Router")
        if r.get("ieee_address") == profile["ieee"]:
            raise ValueError("join_via must be distinct from target")

        open_tx = token + "-open"
        client.publish(
            base + "/bridge/request/permit_join",
            json.dumps({"time": seconds, "device": router_name, "transaction": open_tx}),
            qos=1,
        ).wait_for_publish(5)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and open_tx not in state["permit_responses"]:
            wake.wait(0.3)
            wake.clear()
        open_response = state["permit_responses"].get(open_tx)
        if not open_response or open_response.get("status") != "ok":
            raise RuntimeError("Scoped permit-join not confirmed: " + repr(open_response))
        state["opened"] = True
        print("SOURCE_JOIN_WINDOW_OPEN", seconds, router_name, flush=True)

        stop = time.monotonic() + seconds
        cadence = float(a.probe_every_seconds)
        while time.monotonic() < stop and not fresh.is_set():
            probes += 1
            client.publish(
                base + "/" + profile["device"] + "/get",
                json.dumps({profile.get("relay_get_key", "state"): ""}),
                qos=1,
            ).wait_for_publish(5)
            fresh.wait(min(cadence, max(0.1, stop - time.monotonic())))
        if fresh.is_set():
            result = "fresh_link_restored"
            print("SOURCE_LINK_RESTORED", profile["ieee"], "probes", probes, flush=True)
    finally:
        if state["opened"]:
            close_tx = token + "-close"
            try:
                client.publish(
                    base + "/bridge/request/permit_join",
                    json.dumps({"time": 0, "transaction": close_tx}),
                    qos=1,
                ).wait_for_publish(5)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline and close_tx not in state["permit_responses"]:
                    wake.wait(0.3)
                    wake.clear()
                close_response = state["permit_responses"].get(close_tx)
                print("SOURCE_JOIN_WINDOW_CLOSE", close_response, flush=True)
            except Exception as error:
                print("CRITICAL_JOIN_CLOSE_FAILED", repr(error), flush=True)
        client.loop_stop()
        client.disconnect()

    evidence = {
        "observed_at": now(),
        "result": result if close_response and close_response.get("status") == "ok" else "unconfirmed",
        "device": profile["device"],
        "ieee": profile["ieee"],
        "join_via": router_name,
        "window_seconds": seconds,
        "probes": probes,
        "open_response": open_response,
        "close_response": close_response,
        "fresh_target": state["target"],
        "target_events": state["events"],
        "note": "Reachability only; source reconciliation must still prove role/build/quiet OTA/candidate.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as handle:
        json.dump(evidence, handle, indent=2, default=str)
        handle.write("\n")
    print("SOURCE_REJOIN_EVIDENCE", output, flush=True)
    if evidence["result"] != "fresh_link_restored":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
