"""OTA block size: 48 bytes whenever responses are paced.

Zigbee2MQTT never sends a block larger than the device requests
(zigbee-herdsman sendImageBlockResponse serves min(server cap, request)),
so a 48-byte cap can only send what the downloading firmware asked for:

- Custom BSEED firmware's Telink OTA client always requests exactly 48
  bytes (tl_zigbee_sdk V3.7.2.0, OTA_IMAGE_MAX_DATA_SIZE). A paced 48-byte
  transfer completed on custom firmware (SKILL.md, 2026-09-24).
- Stock b28wrpvx firmware requested 64 bytes and accepted 823 consecutive
  50-byte blocks before a single-offset stall
  (docs/bseed_kitchenleft_ota_abort_triage_20260920.md). That run was
  unpaced, and its cause was never established.

Every recorded ~48-50-byte failure predates response pacing, so blocks
above 32 bytes require response_delay_ms >= 1200. A stalled transfer keeps
its partial image and can resume at 32 bytes without losing progress.
Exact historical authorizations stay pinned at 32 in their own gates.
See docs/bseed_ota_transfer_speed_20261117.md.
"""

EVIDENCED_BLOCK_BYTES = 32
MAX_BLOCK_BYTES = 48
PACED_RESPONSE_DELAY_MS = 1200
GATE_BLOCK_BYTES = (EVIDENCED_BLOCK_BYTES, MAX_BLOCK_BYTES)


def _paced(profile):
    delay = profile.get('response_delay_ms')
    return (not isinstance(delay, bool) and isinstance(delay, int)
            and delay >= PACED_RESPONSE_DELAY_MS)


def default_block_bytes(profile):
    """Largest block size the policy allows when a profile leaves it unset.

    Without this the runner falls back to its own 50-byte default, which
    may be unpaced.
    """
    return MAX_BLOCK_BYTES if _paced(profile) else EVIDENCED_BLOCK_BYTES


def check_block_bytes(profile, allowed=None):
    """Return the profile's block size, or raise if it is unproven as configured.

    allowed=None checks only the rule for sizes above 32 and accepts an
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
    if block > EVIDENCED_BLOCK_BYTES and not _paced(profile):
        raise ValueError('Blocks above 32 bytes are proven only with response_delay_ms >= %d'
                         % PACED_RESPONSE_DELAY_MS)
    return block
