"""Exact legacy Router-v8 relay policy compatibility for one BSEED OTA.

The original v8 firmware returns a fresh relay state but reports an explicit
null physical relay mode. Do NOT convert that unknown policy into follow_state
or infer the physical output from cached/offline MQTT. The exception is
pinned to the actual installed image, device and sealed destination.
It never waives backend-correlated fresh Zigbee relay reads.
"""

BALCONY_LEGACY_POLICY = "legacy_unreported_v8_router"
BALCONY_IDENTITY = {
    "device": "BedroomSocketBalcony",
    "ieee": "0xa4c138ac993cc245",
    "manufacturer": "o1jzcxou",
    "model": "TS011F-BS",
    "preflash_role": "Router",
    "postflash_role": "Router",
    "preflash_build": "1.1.3-bseedv8",
    "postflash_build": "1.1.3-bseedr18",
    "sha256": "4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9",
}


# v8 remains a Router, but its bootloader sends query-image type 65026
# (legacy Client family). A read-only, three-candidate OTA probe confirmed
# this on IEEE 0xa4c138ac993cc245 on 2026-11-15. The following transport
# changes ONLY the OTA outer image-type field, while preserving the sealed
# r18 Router native payload and native version. The different outer SHA is
# authorized for this exact dual OTA test only; no SWire recovery is proven.
BALCONY_COMPAT_65026 = dict(
    **{k: v for k,v in BALCONY_IDENTITY.items() if k != 'sha256'},
    sha256='347a27e8fc53053f56d83c77227df87dc7397e1a77d23a4c9ecb6d078668dafe',
    image_type=65026,
    preflash_query_image_type=65026,
    native_sha256='4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9',
    file_version='0x1102301d',
    parallel_ota_max=2,
    concurrent_ota_ieees=['0xa4c13824a7005afb'],
    source_rejoin_strategy='none',
    block_bytes=32,
    response_delay_ms=1800,
    force_test_transition=False,
)


def is_exact_balcony_router_candidate(profile):
    standard = all(profile.get(key) == value for key, value in BALCONY_IDENTITY.items())
    compat = all(profile.get(key) == value for key, value in BALCONY_COMPAT_65026.items())
    if compat:
        # Validate both source-query compatibility and preserved native bytes;
        # an arbitrary wrapper hash must not grant recovery-policy authority.
        from pathlib import Path
        import hashlib, struct
        try:
            native = Path(profile.get('native_image') or '').read_bytes()
            transport = Path(profile.get('image') or '').read_bytes()
            if (hashlib.sha256(native).hexdigest() != BALCONY_COMPAT_65026['native_sha256']
                    or hashlib.sha256(transport).hexdigest() != BALCONY_COMPAT_65026['sha256']
                    or transport[56:] != native[56:]):
                return standard
            if (struct.unpack_from('<HHI',native,10) != (4417,43555,285356061)
                    or struct.unpack_from('<HHI',transport,10) != (4417,65026,285356061)):
                return standard
        except (OSError, TypeError, ValueError, struct.error):
            return standard
    return standard or compat


ENTRANCE_IDENTITY = {
    "device": "WRSocketEntrance",
    "ieee": "0xa4c1387b13e9436d",
    "manufacturer": "o1jzcxou",
    "model": "TS011F-BS",
    "preflash_role": "Router",
    "postflash_role": "Router",
    "preflash_build": "1.1.3-bseedv8",
    "postflash_build": "1.1.3-bseedr18",
    "sha256": "4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9",
}


def is_exact_entrance_router_candidate(profile):
    return all(profile.get(key) == value for key, value in ENTRANCE_IDENTITY.items())


def exact_same_role_nonpm_router_admission(profile):
    """Meterless Router is admitted only for pinned devices/images, never by role alone."""
    if not isinstance(profile, dict):
        return False
    return (profile.get("non_pm") is True and
            profile.get("require_pm") is False and
            profile.get("expect_relay") == "OFF" and
            profile.get("relay_get_key") == "state_relay" and
            profile.get("block_bytes") == 32 and
            profile.get("force_test_transition") is False and
            (is_exact_entrance_router_candidate(profile) or
             is_exact_balcony_router_candidate(profile)))


def verify_relay_policy_profile(profile):
    """Fail closed for any foreign attempt to use the null-policy exception."""
    expected = profile.get("preflash_relay_physical_mode")
    if expected == BALCONY_LEGACY_POLICY:
        if not is_exact_balcony_router_candidate(profile):
            raise ValueError("Legacy v8 null relay policy restricted to exact Balcony r18 image")
        if profile.get("relay_get_key") != "state_relay" or profile.get("expect_relay") != "OFF":
            raise ValueError("Legacy v8 Router policy requires verified relay OFF")
        if profile.get("non_pm") is not True or profile.get("require_pm") is not False:
            raise ValueError("Legacy v8 Router policy requires exact non-PM board")
        return True
    if not isinstance(expected, str) or not expected:
        raise ValueError("Expected physical relay policy must be pinned")
    return False


def relay_policy_matches(fresh_mqtt_state, profile):
    """A read-only policy match: no transition or output writes."""
    legacy = verify_relay_policy_profile(profile)
    if not isinstance(fresh_mqtt_state, dict):
        return False
    if legacy:
        return ("relay_physical_mode" in fresh_mqtt_state
                and fresh_mqtt_state["relay_physical_mode"] is None)
    return fresh_mqtt_state.get("relay_physical_mode") == profile["preflash_relay_physical_mode"]
