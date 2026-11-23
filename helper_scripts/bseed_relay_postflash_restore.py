"""Restore only a verified preflash-ON relay after reboot, never issue OFF.

Only called after successful OTA/reinterview identity verification. For stock
canaries the existing exact two-device restoration owns the process instead.
A successful ON restoration does NOT prove continuous mains power.
"""
import datetime as dt
import json
from pathlib import Path
import threading
import uuid

from bseed_operator_load_policy import connected_load_authorized

STAGES = ("after_rejoin", "after_state_restore", "before_acceptance")


def verify_live_custom_identity(profile):
    """Exact IEEE/build/model plus live ZDO role: do not trust a cached Router label."""
    import paho.mqtt.client as mqtt
    import yaml

    config = yaml.safe_load(Path(profile["mqtt_config"]).read_text(encoding="utf8"))["mqtt"]
    base = config.get("base_topic", "zigbee2mqtt")
    topic = base + "/bridge/devices"
    event = threading.Event()
    candidates = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="bseed-post-reboot-identity-" + uuid.uuid4().hex[:8])
    client.username_pw_set(config.get("user", ""), config.get("password", ""))

    def on_msg(_c, _u, msg):
        if msg.topic != topic:
            return
        try:
            devices = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(devices, list):
            candidates[:] = [d for d in devices if d.get("ieee_address") == profile["ieee"]]
            event.set()

    client.on_message = on_msg
    client.connect(profile["broker"], 1883, 10)
    client.subscribe(topic, 1)
    client.loop_start()
    try:
        if not event.wait(10):
            raise ValueError("Postflash bridge inventory unavailable")
    finally:
        client.loop_stop()
        client.disconnect()
    if len(candidates) != 1:
        raise ValueError("Postflash exact IEEE missing/duplicated")
    live = candidates[0]
    from bseed_socket_version_policy import split_manufacturer
    manufacturer = profile.get("postflash_manufacturer") or split_manufacturer(profile["manufacturer"])[0]
    expected_model = profile.get("postflash_model") or profile["model"]
    for key, expected in (
            ("friendly_name", profile["device"]),
            ("software_build_id", profile["postflash_build"]),
            ("manufacturer", manufacturer),
            ("model_id", expected_model),
            ("interview_state", "SUCCESSFUL")):
        if live.get(key) != expected:
            raise ValueError("Postflash device identity mismatch: " + key)
    address = live.get("network_address")
    if type(address) is not int:
        raise ValueError("Postflash missing device network address")
    from bseed_zdo_live import read_node_descriptor
    zdo = read_node_descriptor(profile["mqtt_config"], profile["broker"], profile["ieee"],
                               address, timeout=20)
    if zdo.get("role") != profile["postflash_role"]:
        raise ValueError("Live ZDO postflash role mismatch")
    return {"reported_build": live["software_build_id"],
            "z2m_cached_role": live.get("type"),
            "live_zdo_role": zdo["role"],
            "nwk_address": address}


def restore_preserved_on(profile, stage, client=None, identity_checker=None):
    """Idempotent exact-target ON-only recovery, with permanent negative continuity evidence."""
    if not connected_load_authorized(profile):
        return None
    if profile.get("stock_client_preserve_loaded") is True:
        # Exact legacy conversion has independent pinned restoration evidence.
        return None
    if stage not in STAGES:
        raise ValueError("Unknown postflash ON recovery stage")
    if profile.get("expect_relay") != "ON":
        # A previously OFF outlet must NEVER be driven ON, nor commanded OFF.
        return None
    work = Path(profile["workdir"])
    lock = json.loads((work / "ACTIVE_LOCK.json").read_text(encoding="utf8"))
    for key, expected in (("device", profile["device"]), ("ieee", profile["ieee"]),
                          ("sha256", profile["sha256"])):
        if lock.get(key) != expected:
            raise ValueError("Postflash relay restoration refuses foreign lock: " + key)
    if lock.get("phase") not in ("ota_transfer_ok_postflash_unverified", "postflash_candidate"):
        raise ValueError("Postflash relay restoration requires completed OTA")
    identity = (identity_checker or verify_live_custom_identity)(profile)
    evidence = work / ("PRESERVED_ON_" + stage.upper() + ".json")
    if evidence.exists():
        record = json.loads(evidence.read_text(encoding="utf8"))
        if (record.get("ieee"), record.get("image_sha256"), record.get("stage")) != (
                profile["ieee"], profile["sha256"], stage):
            raise ValueError("Old relay restoration evidence belongs to another update")
        # An existing negative continuity record is never overwritten.
        return record
    from bseed_relay_auto_off import RelayClient
    own = client is None
    client = client or RelayClient(profile)
    try:
        before = client.read("state_relay")
        if before not in ("ON", "OFF"):
            raise ValueError("Exact-device relay GET unverified")
        if before == "OFF":
            client.set("state_relay", "ON")  # ONLY permitted mutation
        after = client.read("state_relay")
        if after != "ON":
            raise ValueError("ON relay restoration not confirmed")
    finally:
        if own:
            client.close()
    record = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "device": profile["device"], "ieee": profile["ieee"],
        "image_sha256": profile["sha256"], "stage": stage,
        "preflash_relay": "ON", "observed_after_reboot": before,
        "recovery_on_command_sent": before == "OFF", "fresh_relay_after": after,
        "identity": identity, "mains_continuity_proven": False,
        "hardware_acceptance": False,
    }
    with evidence.open("x", encoding="utf8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    print("RELAY_PRESERVE_ON_EVIDENCE", json.dumps(record), flush=True)
    return record
