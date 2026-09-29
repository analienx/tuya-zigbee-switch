"""Recover reachability of one exact OTA campaign target via permit-join.

This helper is generic across BSEED devices and supports four deterministic
rejoin strategies:

- none: refuse to open permit-join;
- scoped: open permit-join only through profile.join_via;
- all: open a bounded network-wide permit-join window;
- auto: prefer a verified join_via router and optionally fall back to Join All.

It never flashes, resets, removes, re-interviews, binds, or restarts Zigbee.
Success means only that a fresh read from the exact target IEEE was observed.
Canonical source reconciliation must still prove role/build, OTA quietness and
candidate identity before any firmware retry is allowed.
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
JOIN_STRATEGIES = ("none", "scoped", "coordinator", "all", "auto")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def target_identity_ok(device: dict, profile: dict) -> bool:
    return (
        device.get("friendly_name"),
        device.get("ieee_address"),
        device.get("manufacturer"),
        device.get("model_id"),
    ) == (
        profile["device"],
        profile["ieee"],
        profile["manufacturer"],
        profile["model"],
    )


def verified_router(devices: list[dict], router_name: str | None, target_ieee: str) -> dict | None:
    if not router_name:
        return None
    matches = [d for d in devices if d.get("friendly_name") == router_name]
    if len(matches) != 1:
        return None
    router = matches[0]
    interview_ok = (
        router.get("interview_completed") is True
        or router.get("interview_state") == "SUCCESSFUL"
    )
    if (
        router.get("type") != "Router"
        or not interview_ok
        or router.get("ieee_address") == target_ieee
    ):
        return None
    return router


def resolve_join_plan(
    strategy: str,
    *,
    router_available: bool,
    allow_join_all_fallback: bool,
) -> list[str]:
    if strategy not in JOIN_STRATEGIES:
        raise ValueError(f"Unknown join strategy {strategy!r}")
    if strategy == "none":
        return []
    if strategy == "scoped":
        if not router_available:
            raise ValueError("Scoped join requested but no verified join_via router is available")
        return ["scoped"]
    if strategy == "coordinator":
        return ["coordinator"]
    if strategy == "all":
        return ["all"]

    # auto: use the narrowest mechanism first. Coordinator-only is always
    # available while the bridge itself is online; Join All is the broadest
    # fallback and therefore remains explicit.
    plan = ["scoped"] if router_available else []
    plan.append("coordinator")
    if allow_join_all_fallback:
        plan.append("all")
    return plan


def adapter_transport_error(response: dict | None) -> bool:
    error = str((response or {}).get("error", ""))
    return "SRSP" in error and "after 6000ms" in error


def permit_payload(mode: str, *, seconds: int, transaction: str, router_name: str | None) -> dict:
    payload = {"time": seconds, "transaction": transaction}
    if mode == "scoped":
        if not router_name:
            raise ValueError("Scoped permit-join requires router_name")
        payload["device"] = router_name
    elif mode == "coordinator":
        payload["device"] = "coordinator"
    elif mode != "all":
        raise ValueError(f"Unsupported permit-join mode {mode!r}")
    return payload


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--confirm-ieee", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--seconds", type=int)
    p.add_argument("--probe-every-seconds", type=int, default=5)
    p.add_argument("--join-strategy", choices=JOIN_STRATEGIES)
    p.add_argument("--allow-join-all-fallback", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> None:
    a = arguments(argv)
    profile = load_profile(Path(a.profile).expanduser().resolve())
    if a.confirm_ieee != profile["ieee"]:
        raise ValueError("Exact target IEEE confirmation required")

    seconds = int(a.seconds if a.seconds is not None else profile.get("join_seconds", 120))
    if not 30 <= seconds <= 180:
        raise ValueError("Join window must be 30..180 seconds")
    if not 2 <= a.probe_every_seconds <= 30:
        raise ValueError("Probe cadence must be 2..30 seconds")

    requested_strategy = a.join_strategy or profile.get("source_rejoin_strategy", "auto")
    if requested_strategy not in JOIN_STRATEGIES:
        raise ValueError("source_rejoin_strategy must be one of none/scoped/all/auto")
    allow_global = bool(
        a.allow_join_all_fallback or profile.get("allow_join_all_fallback", False)
    )

    work = Path(profile["workdir"])
    lock_path = work / "ACTIVE_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf8"))
    if (lock.get("device"), lock.get("ieee"), lock.get("sha256")) != (
        profile["device"], profile["ieee"], profile["sha256"]
    ):
        raise ValueError("Campaign lock identity/hash mismatch")
    if lock.get("phase") not in ELIGIBLE_PHASES:
        raise ValueError("Campaign phase is not eligible for source-link recovery")

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
                if len(state["events"]) < 160:
                    state["events"].append({"at": now(), "message": text[:400]})
        wake.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(profile["broker"], 1883, 10)
    client.loop_start()

    attempts: list[dict] = []
    result = "unconfirmed"
    chosen_mode = None
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

        targets = [d for d in devices if d.get("ieee_address") == profile["ieee"]]
        if len(targets) != 1 or not target_identity_ok(targets[0], profile):
            raise ValueError("Cached target identity is absent, ambiguous or mismatched")

        router_name = profile.get("join_via")
        router = verified_router(devices, router_name, profile["ieee"])
        plan = resolve_join_plan(
            requested_strategy,
            router_available=router is not None,
            allow_join_all_fallback=allow_global,
        )
        if not plan:
            raise RuntimeError("Source-link recovery disabled by join strategy 'none'")

        fatal_error = None
        for mode in plan:
            fresh.clear()
            state["target"] = None
            open_tx = f"{token}-{mode}-open"
            close_tx = f"{token}-{mode}-close"
            open_response = None
            close_response = None
            probes = 0
            open_attempted = False
            opened = False
            attempt = {
                "mode": mode,
                "router": router_name if mode == "scoped" else None,
                "window_seconds": seconds,
                "started_at": now(),
            }
            try:
                payload = permit_payload(
                    mode,
                    seconds=seconds,
                    transaction=open_tx,
                    router_name=router_name,
                )
                open_attempted = True
                client.publish(
                    base + "/bridge/request/permit_join",
                    json.dumps(payload),
                    qos=1,
                ).wait_for_publish(5)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and open_tx not in state["permit_responses"]:
                    wake.wait(0.3)
                    wake.clear()
                open_response = state["permit_responses"].get(open_tx)
                attempt["open_response"] = open_response
                if not open_response or open_response.get("status") != "ok":
                    if adapter_transport_error(open_response):
                        raise RuntimeError(
                            f"ADAPTER_TRANSPORT:{mode} permit-join failed: {open_response!r}"
                        )
                    raise RuntimeError(
                        f"{mode} permit-join not confirmed: {open_response!r}"
                    )
                opened = True
                print(
                    "SOURCE_JOIN_WINDOW_OPEN",
                    mode,
                    seconds,
                    router_name if mode == "scoped" else "ALL",
                    flush=True,
                )

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
                attempt["probes"] = probes
                attempt["fresh_exact_target"] = fresh.is_set()
                if fresh.is_set():
                    result = "fresh_link_restored"
                    chosen_mode = mode
                    print(
                        "SOURCE_LINK_RESTORED",
                        profile["ieee"],
                        "mode",
                        mode,
                        "probes",
                        probes,
                        flush=True,
                    )
            except RuntimeError as error:
                attempt["error"] = str(error)
                if str(error).startswith("ADAPTER_TRANSPORT:"):
                    fatal_error = str(error)
            finally:
                if open_attempted:
                    try:
                        client.publish(
                            base + "/bridge/request/permit_join",
                            json.dumps({"time": 0, "transaction": close_tx}),
                            qos=1,
                        ).wait_for_publish(5)
                        deadline = time.monotonic() + 12
                        while (
                            time.monotonic() < deadline
                            and close_tx not in state["permit_responses"]
                        ):
                            wake.wait(0.3)
                            wake.clear()
                        close_response = state["permit_responses"].get(close_tx)
                        attempt["close_response"] = close_response
                        if not close_response or close_response.get("status") != "ok":
                            fatal_error = fatal_error or (
                                f"JOIN_CLOSE_UNCONFIRMED:{mode}:{close_response!r}"
                            )
                        print(
                            "SOURCE_JOIN_WINDOW_CLOSE",
                            mode,
                            close_response,
                            flush=True,
                        )
                    except Exception as error:
                        attempt["close_error"] = repr(error)
                        fatal_error = fatal_error or f"JOIN_CLOSE_EXCEPTION:{mode}:{error!r}"
                        print("CRITICAL_JOIN_CLOSE_FAILED", mode, repr(error), flush=True)
                attempt["finished_at"] = now()
                attempts.append(attempt)

            if fatal_error:
                break
            if result == "fresh_link_restored":
                if not close_response or close_response.get("status") != "ok":
                    result = "unconfirmed"
                break
    finally:
        client.loop_stop()
        client.disconnect()

    evidence = {
        "observed_at": now(),
        "result": result,
        "device": profile["device"],
        "ieee": profile["ieee"],
        "requested_strategy": requested_strategy,
        "allow_join_all_fallback": allow_global,
        "strategy_used": chosen_mode,
        "join_via": profile.get("join_via"),
        "attempts": attempts,
        "fatal_error": fatal_error,
        "fresh_target": state["target"],
        "target_events": state["events"],
        "note": (
            "Reachability only; source reconciliation must still prove "
            "role/build/quiet OTA/candidate."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as handle:
        json.dump(evidence, handle, indent=2, default=str)
        handle.write("\n")
    print("SOURCE_REJOIN_EVIDENCE", output, flush=True)
    if fatal_error:
        raise SystemExit(3)
    if evidence["result"] != "fresh_link_restored":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
