"""Fail-closed live BSEED PM load-cycle gate. No socket switching or OTA.

An independently controlled, appropriately rated TEST load is required. The
fixture MUST NOT interrupt the BSEED socket's own power or Zigbee router.
Explicit ZCL reads are permitted only to establish the initial baseline;
loaded and unloaded phases pass exclusively on fresh unsolicited MQTT states.
"""
import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    timestamp: float
    retained: bool
    data: dict


def valid_sample(sample, phase, *, min_load_w=5, max_idle_w=1):
    """Return (passed, reason); never accept retained or incomplete values."""
    if sample.retained:
        return False, "retained MQTT state is not a fresh measurement"
    data = sample.data
    if not all(k in data for k in ("power", "current", "voltage", "energy", "state_relay")):
        return False, "missing standard metering or relay property"
    try:
        power, current, voltage, energy = (float(data[k]) for k in ("power", "current", "voltage", "energy"))
    except (TypeError, ValueError):
        return False, "invalid numeric measurement"
    if not all(map(math.isfinite, (power, current, voltage, energy))):
        return False, "non-finite measurement"
    if not 180 <= voltage <= 260 or energy < 0 or data["state_relay"] != "ON":
        return False, "voltage, energy, or relay is outside the test contract"
    if phase == "loaded" and power >= min_load_w and current > 0:
        return True, "loaded measurement received"
    if phase == "unloaded" and 0 <= power <= max_idle_w and 0 <= current <= 0.02:
        return True, "zero-load measurement received"
    return False, f"{phase} measurement wrong: {power} W / {current} A"


def next_unsolicited(samples, started, phase, *, min_load_w=5, max_idle_w=1):
    """Use only state events received after the physical fixture transition."""
    for sample in samples:
        if sample.timestamp < started:
            continue
        ok, _ = valid_sample(sample, phase, min_load_w=min_load_w, max_idle_w=max_idle_w)
        if ok:
            return sample
    return None


def reporting_snapshot(bridge_device):
    endpoint = bridge_device.get("endpoints", {}).get("1", {})
    rows = endpoint.get("configured_reportings", [])
    return [row for row in rows if row.get("cluster") in (2820, "haElectricalMeasurement")]


def ha_matches(value, phase, min_load_w, max_idle_w):
    try:
        watts = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(watts):
        return False
    return watts >= min_load_w if phase == "loaded" else 0 <= watts <= max_idle_w


def wait_for_ha_power(base_url, entity_id, token, phase, min_load_w, max_idle_w, timeout=25):
    """Read HA's existing power entity; never send commands to the DUT."""
    import urllib.parse
    import urllib.request
    path = "/api/states/" + urllib.parse.quote(entity_id, safe="")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=4) as response:
                state = json.load(response).get("state")
            if ha_matches(state, phase, min_load_w, max_idle_w):
                return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False

class Probe:
    def __init__(self, client, base, name):
        self.client, self.base, self.name = client, base, name
        self.events, self.device, self.connected = [], None, False
        self.topic = f"{base}/{name}"
        client.on_connect = self.on_connect
        client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason != 0:
            raise RuntimeError(f"MQTT connection failed: {reason}")
        client.subscribe([(self.topic, 0), (f"{self.base}/bridge/devices", 0)])
        self.connected = True

    def on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if message.topic == self.topic and isinstance(data, dict):
            self.events.append(Sample(time.monotonic(), message.retain, data))
        if message.topic == f"{self.base}/bridge/devices" and isinstance(data, list):
            self.device = next((d for d in data if d.get("friendly_name") == self.name), None)

    def wait(self, phase, start, timeout, min_load_w, max_idle_w):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = next_unsolicited(self.events, start, phase, min_load_w=min_load_w, max_idle_w=max_idle_w)
            if result is not None:
                return result
            time.sleep(0.2)
        return None

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True, help="One PM socket friendly name")
    ap.add_argument("--expected-role", choices=["EndDevice", "Router"], required=True)
    ap.add_argument("--firmware-tag", default="-bseed", help="Required substring in installed firmware identity")
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--base", default="zigbee2mqtt")
    ap.add_argument("--mqtt-config", type=Path, help="Optional local Zigbee2MQTT YAML (never logged)")
    ap.add_argument("--fixture-topic", required=True, help="Independent, dedicated load-controller set topic")
    ap.add_argument("--fixture-on-json", required=True)
    ap.add_argument("--fixture-off-json", required=True)
    ap.add_argument("--allow-fixture", action="store_true", help="Confirm test fixture is safe to switch and does NOT power the DUT")
    ap.add_argument("--min-load-w", type=float, default=5)
    ap.add_argument("--max-idle-w", type=float, default=1)
    ap.add_argument("--phase-timeout-s", type=float, default=35)
    ap.add_argument("--energy-timeout-s", type=float, default=900)
    ap.add_argument("--energy-min-kwh", type=float, default=0.001)
    ap.add_argument("--max-report-s", type=int, default=60)
    ap.add_argument("--ha-url", help="Optional Home Assistant URL for the existing sensor")
    ap.add_argument("--ha-power-entity", help="Existing Home Assistant power sensor entity ID")
    ap.add_argument("--ha-token-env", default="BSEED_HA_TOKEN", help="Name of environment variable containing HA token")
    ap.add_argument("--require-ha", action="store_true", help="Fail if live HA sensor cannot be checked")
    args = ap.parse_args(argv)
    token = os.getenv(args.ha_token_env)
    ha_enabled = bool(args.ha_url and args.ha_power_entity and token)
    if args.require_ha and not ha_enabled:
        ap.error("--require-ha needs --ha-url, --ha-power-entity and an HA token in the selected environment variable")
    if (not args.allow_fixture or not args.fixture_topic.startswith(args.base + "/") or not args.fixture_topic.endswith("/set") or args.fixture_topic.count("/") != args.base.count("/") + 2 or args.fixture_topic == f"{args.base}/{args.device}/set"):
        ap.error("dedicated independent test fixture must be explicitly authorized; NEVER switch DUT relay")
    try:
        on, off = json.loads(args.fixture_on_json), json.loads(args.fixture_off_json)
        if not isinstance(on, dict) or not isinstance(off, dict) or on == off:
            ap.error("fixture on/off must be distinct JSON objects")
    except ValueError as exc:
        ap.error(f"invalid fixture JSON: {exc}")
    import paho.mqtt.client as mqtt
    user, password = os.getenv("BSEED_MQTT_USER"), os.getenv("BSEED_MQTT_PASSWORD")
    if args.mqtt_config:
        import yaml
        settings = yaml.safe_load(args.mqtt_config.read_text())["mqtt"]
        user, password = settings.get("user", user), settings.get("password", password)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if user is not None:
        client.username_pw_set(user, password)
    probe = Probe(client, args.base, args.device)
    client.connect(args.broker, args.port, 10)
    client.loop_start()
    fixture_armed = False
    try:
        deadline = time.monotonic() + 12
        while (not probe.connected or probe.device is None) and time.monotonic() < deadline:
            time.sleep(0.2)
        if probe.device is None:
            raise RuntimeError("DUT absent from Zigbee2MQTT inventory")
        print("DUT", args.device, "ROLE", probe.device.get("type"), "FIRMWARE", probe.device.get("software_build_id"), flush=True)
        if probe.device.get("type") != args.expected_role or args.firmware_tag not in (probe.device.get("software_build_id") or ""):
            raise RuntimeError("FAIL: installed firmware identity or Zigbee role does not match this test")
        reports = reporting_snapshot(probe.device)
        power_report = next((r for r in reports if r.get("attrId") == 1291), None)
        print("POWER_REPORTING_CACHED", power_report, flush=True)
        if not power_report:
            raise RuntimeError("power reporting not configured in Zigbee2MQTT inventory")
        slow_reporting = power_report.get("maxRepIntval", 65535) > args.max_report_s
        if slow_reporting:
            print("FAIL_REPORTING_MAXIMUM_EXCEEDS_FRESHNESS_BUDGET", power_report.get("maxRepIntval"), flush=True)
        # Explicit read only BEFORE the independent fixture load cycle.
        baseline_start = time.monotonic()
        client.publish(probe.topic + "/set", json.dumps({"read": {"cluster": "haElectricalMeasurement", "attributes": ["rmsVoltage", "rmsCurrent", "activePower"]}}), qos=1)
        baseline = probe.wait("unloaded", baseline_start, 18, args.min_load_w, args.max_idle_w)
        if baseline is None:
            raise RuntimeError("initial zero-load ZCL read/baseline failed; fixture must start OFF")
        print("BASELINE_FROM_EXPLICIT_READ_NOT_A_REPORT_PASS", baseline.data.get("power"), flush=True)
        if ha_enabled and not wait_for_ha_power(args.ha_url, args.ha_power_entity, token, "unloaded", args.min_load_w, args.max_idle_w):
            raise RuntimeError("FAIL: Home Assistant was nonzero before fixture ON; stale loaded sensor could falsely pass")
        fixture_armed = True
        loaded_start = time.monotonic()
        client.publish(args.fixture_topic, json.dumps(on), qos=1).wait_for_publish(timeout=4)
        loaded = probe.wait("loaded", loaded_start, args.phase_timeout_s, args.min_load_w, args.max_idle_w)
        if loaded is None:
            raise RuntimeError("FAIL: no unsolicited MQTT loaded wattage after fixture ON")
        print("PASS_LOADED_UNSOLICITED", loaded.data["power"], "W", flush=True)
        if ha_enabled and not wait_for_ha_power(args.ha_url, args.ha_power_entity, token, "loaded", args.min_load_w, args.max_idle_w):
            raise RuntimeError("FAIL: Home Assistant power entity stayed stale under load")
        initial_energy = float(loaded.data["energy"])
        energy_passed = args.energy_min_kwh <= 0
        if args.energy_min_kwh > 0:
            energy_deadline = time.monotonic() + args.energy_timeout_s
            while time.monotonic() < energy_deadline:
                if any(e.timestamp > loaded.timestamp and not e.retained and
                       float(e.data.get("energy", -1)) >= initial_energy + args.energy_min_kwh
                       for e in probe.events):
                    energy_passed = True
                    print("PASS_UNSOLICITED_ENERGY_DELTA", flush=True)
                    break
                time.sleep(0.2)
            else:
                print("FAIL_ENERGY_NOT_AUTOMATICALLY_REPORTED; CHECKING_UNLOAD_ANYWAY", flush=True)
        unloaded_start = time.monotonic()
        client.publish(args.fixture_topic, json.dumps(off), qos=1).wait_for_publish(timeout=4)
        fixture_armed = False
        unloaded = probe.wait("unloaded", unloaded_start, args.phase_timeout_s, args.min_load_w, args.max_idle_w)
        if unloaded is None:
            raise RuntimeError("FAIL: cached nonzero power survived fixture OFF; no explicit read was used")
        if float(unloaded.data["energy"]) < initial_energy:
            raise RuntimeError("FAIL: energy decreased during a single load cycle")
        if ha_enabled and not wait_for_ha_power(args.ha_url, args.ha_power_entity, token, "unloaded", args.min_load_w, args.max_idle_w):
            raise RuntimeError("FAIL: Home Assistant power entity stayed nonzero after fixture OFF")
        if not ha_enabled:
            print("HA_GATE_NOT_CONFIGURED; MQTT_PASS_IS_NOT_FULL_HA_RELEASE", flush=True)
        print("PASS_AUTO_LOAD_TO_ZERO", loaded.data["power"], "->", unloaded.data["power"], "W", flush=True)
        if not energy_passed:
            raise RuntimeError("FAIL: energy did not advance by the required amount through unsolicited MQTT")
        if slow_reporting:
            raise RuntimeError("FAIL: power reporting interval exceeds release freshness budget")
        return 0
    finally:
        # Never leave a test load energized on failure or interruption.
        if fixture_armed:
            try:
                client.publish(args.fixture_topic, json.dumps(off), qos=1).wait_for_publish(timeout=4)
                print("FIXTURE_FAILSAFE_OFF_SENT", flush=True)
            except Exception as exc:
                print("CRITICAL_FIXTURE_OFF_FAILED", type(exc).__name__, flush=True)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print("BSEED_PM_E2E_FAILED", str(exc), flush=True)
        raise SystemExit(1)
