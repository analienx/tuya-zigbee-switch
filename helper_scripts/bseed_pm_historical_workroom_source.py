"""Evidence-bound stale Zigbee Basic label for the *single* historical Workroom PM Router.

Sept 2026 rc5 Router firmware returned a 19-byte swBuildId which Herdsman
rejected. Its cached label still says cli8. This alias never changes sealed
OTA source-version policy: source is rc5 Router 0x12053012, not cli8 Client.
"""
SOURCE = {
    "device": "WorkroomSocketCabinet",
    "ieee": "0xa4c138c5f07ee732",
    "manufacturer": "b28wrpvx",
    "model": "TS011F-BS-PM",
    "preflash_build": "1.2.5-bseedv8u5-rc5",
    "preflash_cached_build": "1.2.5-bseedcli8",
    "preflash_version": 302329874,
    "preflash_role": "Router",
    "postflash_build": "1.2.5-bseedcli22",
    "postflash_role": "EndDevice",
    "sha256": "69976cc008da64da88d0ec6c345b2a059aa5326e4c44de54842a98a5dd5d69f6",
    "native_sha256": "a8d2e38f53c63a4f0561076c1c561a1917f25ece602b16d256ff1b1c36a7bbf8",
    "image_type": 43556,
    "file_version": "0x12053021",
    "force_test_transition": False,
    "pm_preflash_load_proof": "physically_unloaded",
    "join_via": "WRSocketEntrance",
    "require_pm": True,
    "non_pm": False,
    "relay_get_key": "state_relay",
    "expect_relay": "ON",
}


def is_exact_historical_workroom_profile(profile):
    return (isinstance(profile, dict) and
            all(profile.get(k) == v for k, v in SOURCE.items()))


def is_expected_stale_inventory(profile, inventory):
    return (is_exact_historical_workroom_profile(profile)
            and isinstance(inventory, dict)
            and inventory.get("friendly_name") == SOURCE["device"]
            and inventory.get("ieee_address") == SOURCE["ieee"]
            and inventory.get("manufacturer") == SOURCE["manufacturer"]
            and inventory.get("model_id") == SOURCE["model"]
            and inventory.get("type") == "Router"
            and inventory.get("software_build_id") == SOURCE["preflash_cached_build"]
            and inventory.get("interview_state") == "SUCCESSFUL"
            and type(inventory.get("network_address")) is int)


def require_fresh_historical_source(profile, inventory, fresh_relay, zdo):
    """Require exact current OTA version and fresh wire ZDO Router, not just cached Basic."""
    if not is_expected_stale_inventory(profile, inventory):
        raise ValueError("Cabinet stale Basic alias is not authorized for this profile/inventory")
    if not isinstance(fresh_relay, dict) or not isinstance(zdo, dict):
        raise ValueError("Cabinet source requires fresh relay and transaction-correlated ZDO")
    dev = fresh_relay.get("device") or {}
    update = fresh_relay.get("update") or {}
    if (dev.get("ieeeAddr"), dev.get("manufacturerName"), dev.get("type"),
            dev.get("softwareBuildID"), dev.get("dateCode")) != (
            SOURCE["ieee"], SOURCE["manufacturer"], "Router",
            SOURCE["preflash_cached_build"], "20260926"):
        raise ValueError("Cabinet live source identity differs from historical rc5 evidence")
    if (type(update.get("installed_version")) is not int or
            update["installed_version"] != SOURCE["preflash_version"] or
            update.get("state") not in ("idle", "available")):
        raise ValueError("Cabinet source 0x12053012 OTA version is not confirmed")
    if (zdo.get("role"), zdo.get("logical_type"), zdo.get("nwk_address"),
            zdo.get("manufacturer_code")) != (
            "Router", 1, inventory["network_address"], 4417):
        raise ValueError("Cabinet fresh ZDO is not a matching Router")
    if fresh_relay.get(SOURCE["relay_get_key"]) != SOURCE["expect_relay"]:
        raise ValueError("Cabinet relay state does not match physically unloaded baseline")
    return {"source_build": SOURCE["preflash_build"],
            "stale_cached_build": SOURCE["preflash_cached_build"],
            "source_file_version": SOURCE["preflash_version"],
            "live_zdo_role": "Router",
            "ieee": SOURCE["ieee"],
            "physical_load_proof": "requires operator confirmation at flash",
            "identity_evidence_is_not_direct_readback": True}
