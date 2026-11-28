"""Owner-authorized preserve-relay OTA: electrical load is always advisory.

This is a policy switch for *writes*, not an assertion that firmware reboot
can maintain mains continuity. It preserves strict firmware, role, hash, lease,
source-recovery and Zigbee checks, and requires explicit connected-load risk
acceptance in each PRIVATE campaign profile.

Legacy profiles with automatic relay-OFF are intentionally refused for new OTA.
"""


def connected_load_authorized(profile):
    return bool(
        isinstance(profile, dict)
        and profile.get("relay_state_policy") == "preserve"
        and profile.get("auto_relay_off") is False
        and profile.get("operator_connected_load_risk_accepted") is True
    )


def require_preserve_state_for_flash(profile):
    if not isinstance(profile, dict):
        raise ValueError("OTA flash requires a verified private campaign profile")
    if profile.get("auto_relay_off") is not False:
        raise ValueError("OTA flash refuses automatic relay-OFF: replan with preserve-state policy")
    if not connected_load_authorized(profile):
        raise ValueError(
            "OTA flash requires relay_state_policy=preserve and explicit "
            "operator_connected_load_risk_accepted=true; never require unplugging"
        )
    if profile.get("expect_relay") not in ("ON", "OFF"):
        raise ValueError("OTA flash requires a pinned preflash ON/OFF relay baseline")
    if profile.get("relay_get_key") not in ("state", "state_relay"):
        raise ValueError("OTA flash requires exact relay read endpoint")
    return True
