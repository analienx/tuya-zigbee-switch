"""Exact two-socket, preserve-ON stock-PM -> custom-Client OTA risk authority.

A stock Tuya TS011F reports PM fields but lacks the converter's independent
fresh-ZCL timestamp. Never interpret a cached stock wattage as verified zero.
The operator explicitly accepted loaded OTA of THESE two stock sockets on
2026-11-22, and separately instructed us never to command relays OFF.

This exception grants neither general fleet approval nor guaranteed physical
mains continuity during firmware reboot. Every profile must pin the immutable
wrapper and native payload, identity, ON relay and admitted risk. Negative
postflash relay readings remain failures; no relay OFF is authorized.
After verified rejoin, the runner may restore a reboot-cleared relay back ON
with an explicit auditable per-IEEE targeted ON command.
"""
TRANSPORT_SHA = "f3faa5fdeb90dd3451108a11debd2c24c20b9cf658f7bf6fa01c438f6e60f31b"
NATIVE_SHA = "0c762af950cdd791fb561dc3d219c745f294b11e075f55b3f735ebc059308ee1"
TARGETS = {
    "HALLSocketMain": "0xa4c138186a69b78c",
    "LivingRoomSocketWifiRight": "0xa4c138b5e090e7c0",
}


def authorized(profile):
    opt_in = profile.get("stock_client_preserve_loaded")
    if opt_in is None or opt_in is False:
        return False
    if opt_in is not True:
        raise ValueError("stock_client_preserve_loaded must be Boolean")
    expected = {
        "ieee": TARGETS.get(profile.get("device")),
        "manufacturer": "_TZ3000_b28wrpvx",
        "model": "TS011F",
        "preflash_role": "Router",
        "postflash_role": "EndDevice",
        "postflash_build": "1.2.5-bseedcli27",
        "postflash_manufacturer": "b28wrpvx",
        "postflash_model": "TS011F-BS-PM",
        "stock_client_conversion": True,
        "require_pm": True,
        "non_pm": False,
        "force_test_transition": False,
        "pm_preflash_load_proof": "meter",
        "auto_relay_off": False,
        "relay_state_policy": "preserve",
        "expect_relay": "ON",
        "relay_get_key": "state",
        "operator_waive_preflash_pm": True,
        "operator_connected_load_risk_accepted": True,
        "sha256": TRANSPORT_SHA,
        "native_sha256": NATIVE_SHA,
        "image_type": 54179,
        "file_version": "0xffffffff",
        "manufacturer_code": 4417,
    }
    if expected["ieee"] is None:
        raise ValueError("Loaded conversion is only authorized for two exact stock sockets")
    for key, wanted in expected.items():
        if profile.get(key) != wanted:
            raise ValueError("Loaded conversion refused: pinned %s differs" % key)
    if profile.get("preflash_build") not in (None, ""):
        raise ValueError("Stock device cannot declare a custom source build")
    if not profile.get("native_image") or not profile.get("image"):
        raise ValueError("Loaded conversion requires native image and wrapper")
    return True


def restore_pinned_on_after_rejoin(profile, stage, client=None):
    """Only after verified new build/rejoin: restore a reboot-cleared relay ON.

    No OFF command is issued. A precise per-stage private record distinguishes
    a natural ON retention from a forced-on recovery; neither proves continuity.
    """
    if not authorized(profile):
        return None
    if stage not in ("after_rejoin", "after_state_restore", "before_acceptance"):
        raise ValueError("Unknown loaded stock rejoin restoration stage")
    import datetime as dt
    import json
    from pathlib import Path
    work = Path(profile["workdir"])
    lock = json.loads((work / "ACTIVE_LOCK.json").read_text(encoding="utf8"))
    if (lock.get("device"), lock.get("ieee"), lock.get("sha256")) != (
            profile["device"], profile["ieee"], profile["sha256"]):
        raise ValueError("Loaded recovery refuses foreign campaign lock")
    if lock.get("phase") not in ("ota_transfer_ok_postflash_unverified", "postflash_candidate"):
        raise ValueError("Loaded recovery requires completed OTA transaction")
    evidence = work / ("LOADED_RELAY_RESTORE_" + stage.upper() + ".json")
    if evidence.exists():
        raise ValueError("Loaded relay restoration evidence already exists; inspect first")
    from bseed_relay_auto_off import RelayClient
    own = client is None
    client = client or RelayClient(profile)
    try:
        before = client.read("state_relay")
        if before not in ("ON", "OFF"):
            raise ValueError("Unknown custom relay state after rejoin")
        if before == "OFF":
            client.set("state_relay", "ON")
        after = client.read("state_relay")
        if after != "ON":
            raise ValueError("Failed to restore exact target relay ON")
    finally:
        if own:
            client.close()
    record = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "device": profile["device"], "ieee": profile["ieee"],
        "image_sha256": profile["sha256"], "stage": stage,
        "preflash_relay": "ON", "fresh_after_rejoin_before": before,
        "recovery_on_command_sent": before == "OFF",
        "fresh_after_rejoin_after": after,
        "mains_continuity_proven": False, "hardware_acceptance": False,
    }
    with evidence.open("x", encoding="utf8") as file:
        file.write(json.dumps(record, indent=2) + "\n")
    print("EXACT_STOCK_LOADED_RELAY_RESTORED", json.dumps(record), flush=True)
    return record
