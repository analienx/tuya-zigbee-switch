"""Which installed build counts as a campaign's unchanged source."""


def source_build_matches(profile, build):
    """Exact preflash_build, or, for a stock-to-Client conversion (which may not
    pin one), any build that is not custom BSEED firmware. Callers also match
    the stock manufacturer/model/role exactly; the custom image reports
    b28wrpvx / TS011F-BS-PM / EndDevice, so an install cannot pass."""
    if 'preflash_build' in profile:
        return build == profile['preflash_build']
    if profile.get('stock_client_conversion') is not True:
        raise ValueError('Profile pins no preflash_build and is not a stock conversion')
    if not str(profile.get('manufacturer', '')).startswith('_TZ3000_'):
        raise ValueError('Stock conversion profile without a stock manufacturer')
    return build is None or (isinstance(build, str) and 'bseed' not in build.lower())


def source_build_label(profile):
    return profile.get('preflash_build', 'stock')
