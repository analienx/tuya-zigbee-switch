"""Explicit, exact-IEEE PM meter waiver for already installed custom firmware."""
APPROVED = {
    "LivingRoomSocketWifiLeft": ("0xa4c138ba60b92c5f", "Router"),
    "KitchenSocketRight": ("0xa4c138075cd16ed4", "Router"),
    "KitchenSocketLeft": ("0xa4c138241e3de538", "EndDevice"),
}

def validate(profile):
    selected=profile.get("operator_waive_preflash_pm",False)
    if selected is False:
        return False
    if selected is not True:
        raise ValueError("operator_waive_preflash_pm must be Boolean")
    expected=APPROVED.get(profile.get("device"))
    if expected != (profile.get("ieee"),profile.get("preflash_role")):
        raise ValueError("PM waiver is not approved for this device/role")
    if (profile.get("preflash_role") != profile.get("postflash_role")
        or profile.get("relay_state_policy") != "preserve"
        or profile.get("auto_relay_off") is not False
        or profile.get("relay_get_key") != "state_relay"
        or profile.get("pm_preflash_load_proof", "meter") != "meter"
        or profile.get("require_pm") is not True
        or profile.get("non_pm") is True
        or profile.get("force_test_transition") is True):
        raise ValueError("PM waiver is for preserved-relay same-role PM images only")
    return True
