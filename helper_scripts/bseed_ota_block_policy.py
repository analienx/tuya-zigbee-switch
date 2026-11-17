"""OTA block size: 48 bytes only where the downloading firmware is proven to take it.

The firmware that is currently running downloads the image. Custom BSEED
firmware's Telink OTA client always requests OTA_IMAGE_MAX_DATA_SIZE = 48
bytes (tl_zigbee_sdk V3.7.2.0, zigbee/ota/ota.h), so 48 is the largest
block it can use. This repo has completed a 48-byte transfer on custom
firmware once responses were paced at 1200 ms (SKILL.md, 2026-09-24);
every recorded ~48-50-byte failure predates response pacing.

A stock Tuya socket's first conversion is downloaded by Tuya's own
firmware, a different and unexamined client. Its only evidence here is
the unpaced cli6 run: a 50-byte stall, then a completed 32-byte resume.
Stock sources therefore stay at 32 bytes until a stock transfer proves more.
See docs/bseed_ota_transfer_speed_20261117.md.
"""

STOCK_PREFIXES = ('_TZ3000_', '_TZ3002_')
EVIDENCED_BLOCK_BYTES = 32
CUSTOM_CLIENT_BLOCK_BYTES = 48
PACED_RESPONSE_DELAY_MS = 1200
GATE_BLOCK_BYTES = (EVIDENCED_BLOCK_BYTES, CUSTOM_CLIENT_BLOCK_BYTES)


def stock_source(profile):
    """True when stock Tuya firmware, not custom firmware, will download.

    Keyed on the stock manufacturer prefix. Some early custom builds (e.g.
    cli4) also reported a prefixed name; they are treated as stock and kept
    at 32 bytes, which errs on the safe side.
    """
    return str(profile.get('manufacturer', '')).startswith(STOCK_PREFIXES)


def _paced(profile):
    delay = profile.get('response_delay_ms')
    return (not isinstance(delay, bool) and isinstance(delay, int)
            and delay >= PACED_RESPONSE_DELAY_MS)


def default_block_bytes(profile):
    """Largest block size the policy allows when a profile leaves it unset.

    Without this the runner falls back to its own 50-byte default, which is
    unproven for stock firmware and unpaced for custom firmware.
    """
    if not stock_source(profile) and _paced(profile):
        return CUSTOM_CLIENT_BLOCK_BYTES
    return EVIDENCED_BLOCK_BYTES


def check_block_bytes(profile, allowed=None):
    """Return the profile's block size, or raise if its downloader is unproven at it.

    allowed=None checks only the rules for sizes above 32 and accepts an
    absent value; load_profile fills that from default_block_bytes first.
    Gates that admit only evidenced sizes pass allowed=GATE_BLOCK_BYTES.
    """
    raw = profile.get('block_bytes')
    if raw is None:
        if allowed is not None:
            raise ValueError('block_bytes must be set explicitly')
        return None
    if isinstance(raw, bool):
        raise ValueError('block_bytes must be an integer')
    try:
        block = int(raw)
    except (TypeError, ValueError):
        raise ValueError('block_bytes must be an integer') from None
    if allowed is not None and block not in allowed:
        raise ValueError('block_bytes must be one of %s' % ', '.join(map(str, allowed)))
    if block > EVIDENCED_BLOCK_BYTES:
        if stock_source(profile):
            raise ValueError('Stock firmware downloads a first conversion; only 32-byte '
                             'blocks are evidenced for it')
        if not _paced(profile):
            raise ValueError('Blocks above 32 bytes are proven only with response_delay_ms >= %d'
                             % PACED_RESPONSE_DELAY_MS)
    return block
