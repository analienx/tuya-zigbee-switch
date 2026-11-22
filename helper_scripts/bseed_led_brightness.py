"""Fast, exact-device BSEED LED brightness bulk setter using Zigbee2MQTT MQTT.

Discovers current custom firmware and endpoint-qualified led_brightness_switch
exposure from the live bridge. Bounded parallel GET/SET/GET per socket; no
relay SET, OTA operation, firmware changes, or electricity-control keys.

Use --only for a single canary and --percent for future brightness choices.
All changes must be confirmed by an independent post-write targeted GET.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlparse

PROPERTY = "led_brightness_switch"
BUILDS = {
    ("TS011F-BS-PM", "Router"): ("1.2.5", "r", 24, "b28wrpvx"),
    ("TS011F-BS-PM", "EndDevice"): ("1.2.5", "cli", 27, "b28wrpvx"),
    ("TS011F-BS", "Router"): ("1.1.3", "r", 21, "o1jzcxou"),
    ("TS011F-BS", "EndDevice"): ("1.1.3", "c", 19, "o1jzcxou"),
}
BUILD = re.compile(r"^(1\.2\.5|1\.1\.3)-bseed(r|cli|c)(\d+)$")


def brightness_exposed(items):
    for node in items or []:
        if not isinstance(node, dict):
            continue
        if node.get("property") == PROPERTY and (node.get("access", 0) & 3) == 3:
            return True
        if brightness_exposed(node.get("features")):
            return True
    return False


def classify(device):
    """Return firmware family/revision for a genuinely compatible BSEED socket."""
    spec = BUILDS.get((device.get("model_id"), device.get("type")))
    if not spec or device.get("manufacturer") != spec[3]:
        return None
    match = BUILD.fullmatch(str(device.get("software_build_id") or ""))
    if not match or (match[1], match[2]) != spec[:2] or int(match[3]) < spec[2]:
        return None
    if device.get("interview_state") != "SUCCESSFUL":
        return None
    if not brightness_exposed((device.get("definition") or {}).get("exposes")):
        return None
    if not re.fullmatch(r"0x[0-9a-f]{16}", str(device.get("ieee_address") or ""), flags=re.I):
        return None
    if not device.get("friendly_name"):
        return None
    return (spec[0], spec[1], int(match[3]))


def targets_from_inventory(inventory, *, only=(), include_compatible=False):
    """Default: max installed supported build per board+role, not stale stock."""
    if not isinstance(inventory, list):
        raise ValueError("Zigbee2MQTT bridge/devices was not a list")
    eligible = [(d, classify(d)) for d in inventory]
    eligible = [(d, family) for d, family in eligible if family]
    latest = {}
    for _, (series, code, revision) in eligible:
        key = (series, code)
        latest[key] = max(revision, latest.get(key, 0))
    wanted = set(only)
    if wanted and not any(d["friendly_name"] in wanted for d, _ in eligible):
        raise ValueError("Requested names have no eligible latest BSEED firmware")
    chosen = [
        d for d, family in eligible
        if (include_compatible or family[2] == latest[(family[0], family[1])])
        and (not wanted or d["friendly_name"] in wanted)
    ]
    if wanted - {d["friendly_name"] for d in chosen}:
        raise ValueError("Some requested names are stock, old, unsupported or offline")
    if len({d["ieee_address"] for d in chosen}) != len(chosen) or len({
            d["friendly_name"] for d in chosen}) != len(chosen):
        raise ValueError("Duplicate Zigbee devices or ambiguous friendly names")
    return sorted(chosen, key=lambda d: (d["type"] == "EndDevice", d["friendly_name"]))


def active_target_ota(locks: Path, ieee: str) -> bool:
    if not locks.exists():
        raise ValueError("Missing shared OTA authority directory: " + str(locks))
    for path in locks.glob("bseed-zigbee-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return True  # Fail closed if any live authority is unreadable
        if record.get("ieee") == ieee:
            return True
    return False


class Bridge:
    def __init__(self, config, broker):
        from paho.mqtt import client as mqtt
        self.base = config.get("base_topic", "zigbee2mqtt")
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="bseed-led-bulk-" + uuid.uuid4().hex[:8],
        )
        self.client.username_pw_set(config.get("user", ""), config.get("password", ""))
        self.broker = broker
        self.cond = threading.Condition()
        self.connected = threading.Event()
        self.inventory = []
        self.online = False
        self.states = {}

        def on_connect(client, *unused):
            client.subscribe([
                (self.base + "/bridge/devices", 1),
                (self.base + "/bridge/state", 1),
                (self.base + "/+", 1),
            ])
            self.connected.set()

        def on_message(client, userdata, message):
            try:
                payload = json.loads(message.payload)
            except (ValueError, UnicodeDecodeError):
                return
            with self.cond:
                if message.topic == self.base + "/bridge/devices" and isinstance(payload, list):
                    self.inventory = payload
                elif message.topic == self.base + "/bridge/state" and isinstance(payload, dict):
                    self.online = payload.get("state") == "online"
                elif message.topic.startswith(self.base + "/") and not message.retain:
                    name = message.topic[len(self.base) + 1:]
                    if isinstance(payload, dict) and type(payload.get(PROPERTY)) in (int, float):
                        self.states.setdefault(name, []).append((time.monotonic(), payload[PROPERTY]))
                        self.states[name] = self.states[name][-18:]
                self.cond.notify_all()

        self.client.on_connect = on_connect
        self.client.on_message = on_message

    def start(self):
        self.client.connect(self.broker, 1883, 10)
        self.client.loop_start()
        if not self.connected.wait(7):
            raise ValueError("MQTT connection timed out")
        end = time.monotonic() + 8
        with self.cond:
            while not (self.online and self.inventory):
                wait = end - time.monotonic()
                if wait <= 0:
                    raise ValueError("Zigbee2MQTT inventory or online state unavailable")
                self.cond.wait(wait)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def get(self, name, timeout):
        marker = time.monotonic()
        self.publish(name, "get", {PROPERTY: ""})
        end = time.monotonic() + timeout
        with self.cond:
            while True:
                candidates = [v for at, v in self.states.get(name, []) if at > marker]
                if candidates:
                    return candidates[-1]
                wait = end - time.monotonic()
                if wait <= 0:
                    return None
                self.cond.wait(wait)

    def publish(self, name, kind, payload):
        if kind not in ("get", "set") or set(payload) != {PROPERTY}:
            raise ValueError("Only LED-brightness MQTT GET/SET is authorized")
        if kind == "set" and (type(payload[PROPERTY]) is not int or
                              not 1 <= payload[PROPERTY] <= 100):
            raise ValueError("LED setting must be an integer 1..100")
        rc = self.client.publish(
            self.base + "/" + name + "/" + kind,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        rc.wait_for_publish(timeout=5)
        if rc.rc != 0 or not rc.is_published():
            raise ValueError("MQTT did not accept " + kind + " for " + name)


def update_one(bridge, device, percent, locks, timeout):
    name, ieee = device["friendly_name"], device["ieee_address"]
    record = {"device": name, "ieee": ieee, "build": device["software_build_id"],
              "target_percent": percent}
    try:
        if active_target_ota(locks, ieee):
            raise ValueError("Device owns active OTA network lease")
        # Recheck identity under the same live MQTT connection before any write.
        current = next((d for d in bridge.inventory if d.get("ieee_address") == ieee), None)
        if (current is None or current.get("friendly_name") != name or
                current.get("software_build_id") != device["software_build_id"] or
                classify(current) is None or not bridge.online):
            raise ValueError("Current board/build/bridge no longer matches")
        before = bridge.get(name, timeout)
        record["before"] = before
        if before is None:
            raise ValueError("No targeted fresh brightness GET; skipping SET")
        if before == percent:
            record["status"] = "already_verified"
            return record
        # One LED-only SET. Do not trust the optimistic state from this SET.
        bridge.publish(name, "set", {PROPERTY: percent})
        time.sleep(0.7)
        first = bridge.get(name, timeout)
        time.sleep(0.6)
        second = bridge.get(name, timeout)
        record["readbacks"] = [first, second]
        if first == second == percent:
            record["status"] = "set_and_verified"
        else:
            record["status"] = "unverified"
    except Exception as error:
        record["status"] = "skipped_or_failed"
        record["issue"] = str(error)[:250]
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--percent", type=int, required=True, help="1..100")
    parser.add_argument("--mqtt-config", required=True, type=Path)
    parser.add_argument("--broker", help="MQTT host; defaults to server in config")
    parser.add_argument("--network-lock-dir", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--only", action="append", default=[], help="Exact friendly name; repeatable")
    parser.add_argument("--include-compatible", action="store_true",
                        help="Also include compatible custom builds older than latest installed")
    parser.add_argument("--jobs", type=int, default=2, help="Concurrent GET/SET; 1..3 (default 2)")
    parser.add_argument("--timeout", type=float, default=8, help="Single read timeout, 3..20 s")
    parser.add_argument("--dry-run", action="store_true", help="Discover only, do not SET")
    args = parser.parse_args(argv)
    if not 1 <= args.percent <= 100 or not 1 <= args.jobs <= 3 or not 3 <= args.timeout <= 20:
        parser.error("percent=1..100, jobs=1..3 and timeout=3..20 are required")
    import yaml
    config = yaml.safe_load(args.mqtt_config.read_text(encoding="utf-8"))["mqtt"]
    raw_server = str(config.get("server") or "")
    parsed = urlparse(raw_server if "://" in raw_server else "//" + raw_server)
    broker = args.broker or parsed.hostname
    if not broker:
        parser.error("MQTT --broker or mqtt.server in configuration is required")
    bridge = Bridge(config, broker)
    try:
        bridge.start()
        selected = targets_from_inventory(bridge.inventory, only=args.only,
                                          include_compatible=args.include_compatible)
        print("LED_TARGETS", len(selected), [d["friendly_name"] for d in selected], flush=True)
        if not selected:
            raise SystemExit("No custom firmware with LED brightness exposed")
        if args.dry_run:
            return 0
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(update_one, bridge, d, args.percent,
                                   args.network_lock_dir, args.timeout) for d in selected]
            for future in concurrent.futures.as_completed(futures):
                item = future.result()
                results.append(item)
                print("LED_RESULT", item["status"], item["device"], item.get("readbacks", []),
                      item.get("issue", ""), flush=True)
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now().astimezone()
        path = args.evidence_dir / ("led_brightness_" +
            str(args.percent) + "_" + now.strftime("%Y%m%dT%H%M%S") + ".json")
        record = {"at": now.isoformat(), "percent": args.percent, "targets": len(selected),
                  "verified": sum(x["status"] in ("already_verified", "set_and_verified") for x in results),
                  "results": sorted(results, key=lambda x: x["device"])}
        with path.open("x", encoding="utf-8") as file:
            json.dump(record, file, indent=2)
            file.write("\n")
        print("LED_SUMMARY", record["verified"], "/", len(selected), "EVIDENCE", path, flush=True)
        return 0 if record["verified"] == len(selected) else 2
    finally:
        bridge.stop()


if __name__ == "__main__":
    raise SystemExit(main())
