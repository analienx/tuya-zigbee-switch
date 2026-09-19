#!/usr/bin/env python3
"""Capture a Zigbee OTA QueryNextImage tuple and restore Z2M logging."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import yaml


def endpoint(server: str) -> tuple[str, int]:
    parsed = urlparse(server if "://" in server else f"mqtt://{server}")
    return parsed.hostname or "localhost", parsed.port or 1883


def extract_target_ota_tuples(messages: list[dict], target: str) -> list[dict]:
    """Extract target-attributed QueryNextImage tuples from normal or saved logs.

    Never associate an unlabelled controller packet with a friendly name:
    concurrent devices may issue OTA requests during the same capture.
    """
    fields = ("manufacturerCode", "imageType", "fileVersion")
    found: set[tuple[int, int, int]] = set()
    device_marker = f"Received Zigbee message from '{target}'"
    for item in messages:
        payload = item.get("payload") if isinstance(item, dict) else None
        candidates = [item.get("message", ""), item.get("raw", "")]
        if isinstance(payload, dict):
            candidates.append(payload.get("message", ""))
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            # A saved capture stores parsed log objects; a live MQTT capture
            # also exposes raw JSON, sometimes with escaped quote characters.
            if candidate.lstrip().startswith("{"):
                try:
                    decoded = json.loads(candidate)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, dict):
                    candidate = str(decoded.get("message", ""))
            if device_marker not in candidate:
                continue
            # Preserve compatibility with minimal test fixtures lacking a
            # message type, but reject explicitly typed non-OTA traffic.
            if ", type '" in candidate and "commandQueryNextImageRequest" not in candidate:
                continue
            match = re.search(r"\bdata\s+'(\{[^']+\})'", candidate)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or not all(
                type(data.get(field)) is int for field in fields
            ):
                continue
            values = tuple(data[field] for field in fields)
            if 0 <= values[0] <= 65535 and 0 <= values[1] <= 65535 and 0 <= values[2] <= 0xFFFFFFFF:
                found.add(values)
    return [dict(zip(fields, values)) for values in sorted(found)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument(
        "--allow-live-network-changes", action="store_true",
        help="Explicitly authorize Zigbee2MQTT logging changes, possible restarts, and an OTA availability check (never an image transfer).",
    )
    args = ap.parse_args()
    if not args.allow_live_network_changes:
        ap.error("Live logging changes and coordinator restarts are disabled by default; prefer reparse_tongou_ota_capture.py with saved evidence")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    mc = cfg["mqtt"]
    base = mc.get("base_topic", "zigbee2mqtt")
    host, port = endpoint(str(mc.get("server", "mqtt://localhost:1883")))
    advanced = cfg.get("advanced", {})
    original_level = advanced.get("log_level", "info")
    original_debug_mqtt = bool(advanced.get("log_debug_to_mqtt_frontend", False))

    messages: list[dict] = []
    cv = threading.Condition()

    client = mqtt.Client()
    client.username_pw_set(mc.get("user", ""), mc.get("password", ""))

    def on_connect(c, _u, _f, rc):
        if rc:
            raise RuntimeError(f"MQTT connect failed: {rc}")
        for topic in (
            f"{base}/bridge/state",
            f"{base}/bridge/logging",
            f"{base}/bridge/response/options",
            f"{base}/bridge/response/restart",
            f"{base}/bridge/response/device/ota_update/check",
        ):
            c.subscribe(topic)

    def on_message(_c, _u, msg):
        raw = msg.payload.decode(errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        with cv:
            messages.append({"topic": msg.topic, "payload": payload, "raw": raw})
            cv.notify_all()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 10)
    client.loop_start()
    time.sleep(1)

    def publish(suffix: str, payload: dict) -> None:
        client.publish(f"{base}/{suffix}", json.dumps(payload))

    def wait_for(predicate, timeout: float = 30, since: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        with cv:
            while True:
                for item in reversed(messages[since:]):
                    if predicate(item):
                        return item
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for Zigbee2MQTT response")
                cv.wait(min(remaining, 1))

    def set_logging(level: str, debug_mqtt: bool, tx: str) -> bool:
        start = len(messages)
        publish("bridge/request/options", {
            "options": {"advanced": {
                "log_level": level,
                "log_debug_to_mqtt_frontend": debug_mqtt,
            }},
            "transaction": tx,
        })
        item = wait_for(
            lambda x: x["topic"].endswith("/bridge/response/options")
            and isinstance(x["payload"], dict)
            and x["payload"].get("transaction") == tx,
            since=start,
        )
        if item["payload"].get("status") != "ok":
            raise RuntimeError(f"Z2M options failed: {item['payload']}")
        return bool(item["payload"].get("data", {}).get("restart_required"))

    def restart(tx: str) -> None:
        start = len(messages)
        publish("bridge/request/restart", {"transaction": tx})
        wait_for(
            lambda x: x["topic"].endswith("/bridge/state")
            and "online" in x["raw"].lower(),
            timeout=45,
            since=start,
        )

    capture: dict = {
        "target": args.target,
        "original_logging": {
            "log_level": original_level,
            "log_debug_to_mqtt_frontend": original_debug_mqtt,
        },
    }

    try:
        needs_restart = set_logging("debug", True, "tongou-debug-on")
        if needs_restart:
            restart("tongou-debug-restart")
            time.sleep(2)

        start = len(messages)
        publish("bridge/request/device/ota_update/check", {
            "id": args.target,
            "transaction": "tongou-ota-check",
        })
        result = wait_for(
            lambda x: x["topic"].endswith("/bridge/response/device/ota_update/check")
            and isinstance(x["payload"], dict)
            and (
                x["payload"].get("transaction") == "tongou-ota-check"
                or x["payload"].get("data", {}).get("id") == args.target
            ),
            timeout=45,
            since=start,
        )
        time.sleep(2)
        relevant = messages[start:]
        capture["ota_check_response"] = result["payload"]
        capture["logs"] = [
            x["payload"] for x in relevant
            if x["topic"].endswith("/bridge/logging")
            and (
                args.target in x["raw"]
                or "querynextimage" in x["raw"].lower()
                or "ota request" in x["raw"].lower()
                or "imagetype" in x["raw"].lower()
            )
        ]

        capture["ota_tuples"] = extract_target_ota_tuples(relevant, args.target)
    finally:
        try:
            needs_restart = set_logging(
                original_level, original_debug_mqtt, "tongou-debug-off"
            )
            if needs_restart:
                restart("tongou-restore-restart")
        finally:
            client.loop_stop()
            client.disconnect()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(capture, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
