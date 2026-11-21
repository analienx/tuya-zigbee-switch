"""No-disconnect OTA policy for same-role BSEED PM socket campaigns.

The operator must pin the relay's CURRENT state in expect_relay. This
module never sends any relay state-changing command. Power continuity
through a firmware reboot is NOT guaranteed by a state snapshot.
"""
import math

MAX_LOADED_OTA_WATTS = 100.0


def validate(profile):
    mode = profile.get('relay_state_policy')
    if mode is None:
        return False  # Historical profiles remain readable for diagnosis.
    if mode != 'preserve':
        raise ValueError('Unsupported relay_state_policy')
    if profile.get('auto_relay_off') is not False:
        raise ValueError('Preserve policy forbids auto_relay_off')
    if profile.get('expect_relay') not in ('ON', 'OFF'):
        raise ValueError('Preserve policy requires the pinned preflash relay state')
    if profile.get('relay_get_key') not in ('state', 'state_relay'):
        raise ValueError('Preserve policy requires an explicit relay read key')
    if profile.get('preflash_role') != profile.get('postflash_role'):
        raise ValueError('Loaded relay preservation is not verified for cross-role OTA')
    if profile.get('require_pm') is not True or profile.get('non_pm') is True:
        raise ValueError('Preserve-with-load OTA requires fresh power monitoring')
    if profile.get('pm_preflash_load_proof', 'meter') != 'meter':
        raise ValueError('Preserve-with-load OTA cannot use unplugged attestation')
    watts = profile.get('max_reported_watts')
    if type(watts) not in (float, int) or not math.isfinite(watts) or not (0 < watts <= MAX_LOADED_OTA_WATTS):
        raise ValueError('Preserve-with-load OTA requires 0 < max_reported_watts <= 100')
    return True


def verify_after_flash(profile, reader=None):
    """Read only the target's relay; refuse acceptance on mismatch.

    The same fresh GET path is used by the OTA preflight to check
    expect_relay BEFORE flashing. The postflash check adds the other side
    of the invariant; it never toggles a connected appliance.
    """
    if not validate(profile):
        return None
    own = reader is None
    if own:
        from bseed_relay_auto_off import RelayClient
        reader = RelayClient(profile)
    try:
        actual = reader.read(profile['relay_get_key'])
        wanted = profile['expect_relay']
        if actual != wanted:
            raise ValueError('Postflash relay changed: expected %s, observed %s (NO relay command sent)' % (wanted, actual))
        print('RELAY_PRESERVED_POSTFLASH', profile['device'], actual, flush=True)
        return actual
    finally:
        if own:
            reader.close()
