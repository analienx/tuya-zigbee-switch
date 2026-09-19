#!/usr/bin/env python3
"""Non-destructive Zigbee2MQTT discovery probe for Tongou breakers."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import yaml

TONGOU_MANUFACTURER = "_TZ3000_cayepv1a"
TONGOU_MODEL = "TS011F"


def mqtt_endpoint(server: str) -> tuple[str, int]:
    parsed = urlparse(server if "://" in server else f"mqtt://{server}")
    return parsed.hostname or "localhost", parsed.port or 1883


def compact_device(device: dict) -> dict:
    endpoint = (device.get("endpoints") or {}).get("1", {})
    clusters = endpoint.get("clusters") or {}
    return {
        "friendly_name": device.get("friendly_name"),
        "ieee_address": device.get("ieee_address"),
        "network_address": device.get("network_address"),
        "manufacturer": device.get("manufacturer"),
        "model_id": device.get("model_id"),
        "type": device.get("type"),
        "input_clusters": clusters.get("input", []),
        "output_clusters": clusters.get("output", []),
        "supports_ota": (device.get("definition") or {}).get("supports_ota"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target")
    parser.add_argument("--ota-check", action="store_true")
    parser.add_argument("--listen-seconds", type=float, default=35)
    args = parser.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    mqtt_cfg = cfg["mqtt"]
    host, port = mqtt_endpoint(str(mqtt_cfg.get("server", "mqtt://localhost:1883")))
    base = mqtt_cfg.get("base_topic", "zigbee2mqtt")
    devices: list[dict] = []
    events: list[dict] = []

    client = mqtt.Client()
    client.username_pw_set(mqtt_cfg.get("user", ""), mqtt_cfg.get("password", ""))

    def on_connect(c, _u, _f, rc):
        if rc != 0:
            raise RuntimeError(f"MQTT connect failed: {rc}")
        c.subscribe(f"{base}/bridge/devices")
        c.subscribe(f"{base}/bridge/logging")
        c.subscribe(f"{base}/bridge/response/device/ota_update/check")

    def on_message(_c, _u, msg):
        try:
            payload = json.loads(msg.payload.decode(errors="replace"))
        except json.JSONDecodeError:
            return
        if msg.topic == f"{base}/bridge/devices":
            devices[:] = payload
            return
        text = json.dumps(payload, separators=(",", ":"))
        if (args.target and args.target in text) or "ota" in text.lower():
            events.append({"topic": msg.topic, "payload": payload})

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 10)
    client.loop_start()
    time.sleep(2)

    matches = [
        d for d in devices
        if d.get("manufacturer") == TONGOU_MANUFACTURER
        and d.get("model_id") == TONGOU_MODEL
    ]
    print(json.dumps({"devices": [compact_device(d) for d in matches]}, indent=2))

    if args.ota_check:
        if not args.target:
            raise SystemExit("--ota-check requires --target")
        client.publish(
            f"{base}/bridge/request/device/ota_update/check",
            json.dumps({"id": args.target}),
        )
        time.sleep(args.listen_seconds)
        print(json.dumps({"ota_check_events": events}, indent=2))

    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
