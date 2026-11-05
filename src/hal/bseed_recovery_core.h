#ifndef BSEED_RECOVERY_CORE_H
#define BSEED_RECOVERY_CORE_H

#include <stdbool.h>
#include <stdint.h>

/* Shared retry ownership for mains Clients and Routers. The platform adapter
 * starts a single SDK operation only after claim(), and must call finish()
 * when that operation is rejected or reaches a real terminal callback.
 * A timeout alone must not surrender ownership of a potentially live SDK
 * operation. All elapsed comparisons tolerate uint32_t clock rollover. */
typedef struct {
    uint32_t scheduled_at_ms;
    uint32_t delay_ms;
    uint32_t stagger_ms;
    uint32_t pending_since_ms; /* valid only while pending */
    uint32_t generation;       /* internal token, not supplied by the Telink SDK */
    uint32_t last_probe_failure_ms;
    uint8_t  failures;
    uint8_t  probe_failures;
    uint8_t  last_probe_sequence;
    uint8_t  probe_sequence;
    bool     pending;
    bool     probe_pending;
    bool     all_channels_next;
} bseed_recovery_core_t;

typedef enum {
    BSEED_RECOVERY_SECURE_CURRENT_CHANNEL = 0,
    BSEED_RECOVERY_SECURE_ALL_CHANNELS,
} bseed_recovery_rung_t;

#define BSEED_RECOVERY_WEDGE_MS            (10u * 60u * 1000u)
#define BSEED_RECOVERY_PROBE_SPACING_MS    (60u * 1000u)

/* Detect missing SDK completion without releasing radio ownership. The role
 * adapter decides whether a separately budgeted rescue reboot is permitted. */
static inline bool bseed_recovery_wedged(const bseed_recovery_core_t *core,
                                         uint32_t now) {
    return core->pending &&
           (uint32_t)(now - core->pending_since_ms) >= BSEED_RECOVERY_WEDGE_MS;
}

/* A stable, well-mixed full-IEEE hash. The avalanche is important for
 * sequential vendor serial numbers, which must not bunch Router rescans or
 * checkpoint reboots into a few milliseconds. Use separate domain salts for
 * distinct radio operations. Never base a fleet stagger on raw IEEE bytes. */
static inline uint32_t bseed_recovery_hash_ieee(const uint8_t ieee[8],
                                                 uint32_t domain) {
    uint32_t hash = 2166136261u ^ domain;

    for (unsigned i = 0; i < 8u; i++)
        hash = (hash ^ ieee[i]) * 16777619u;
    hash ^= hash >> 16;
    hash *= 0x7feb352du;
    hash ^= hash >> 15;
    hash *= 0x846ca68bu;
    hash ^= hash >> 16;
    return hash;
}

static inline uint32_t bseed_recovery_action_stagger_ms(
    const uint8_t ieee[8], uint32_t domain, uint32_t window_ms) {
    return window_ms ? bseed_recovery_hash_ieee(ieee, domain) % window_ms : 0u;
}

static inline uint32_t bseed_recovery_stagger_ms(const uint8_t ieee[8]) {
    return bseed_recovery_action_stagger_ms(ieee, 0x50524f42u, 30001u);
}

static inline uint32_t bseed_recovery_backoff_ms(uint8_t failures) {
    if (failures <= 1u)
        return 30000u;

    if (failures == 2u)
        return 60000u;

    if (failures == 3u)
        return 120000u;

    return 300000u;
}

static inline uint32_t bseed_recovery_delay_ms(uint8_t failures,
                                               uint32_t stagger_ms) {
    uint32_t base         = bseed_recovery_backoff_ms(failures);
    uint32_t jitter_limit = base / 4u;
    uint32_t jitter       = stagger_ms % (jitter_limit + 1u);

    /* Keep a five-minute maximum without losing the per-device stagger once
     * every device reaches the capped rung. */
    if (base == 300000u)
        return base - jitter;

    return base + jitter > 300000u ? 300000u : base + jitter;
}

static inline void bseed_recovery_init(bseed_recovery_core_t *core,
                                       const uint8_t ieee[8], uint32_t now,
                                       bool immediate) {
    core->scheduled_at_ms       = now;
    core->stagger_ms            = bseed_recovery_stagger_ms(ieee);
    core->delay_ms              = immediate ? 0u : bseed_recovery_delay_ms(0u, core->stagger_ms);
    core->pending_since_ms      = 0u;
    core->generation            = 0u;
    core->last_probe_failure_ms = 0u;
    core->failures              = 0u;
    core->probe_failures        = 0u;
    core->last_probe_sequence   = 0u;
    core->probe_sequence        = 0u;
    core->probe_pending         = false;
    core->pending           = false;
    core->all_channels_next = false;
}

/* Register only an accepted coordinator request, never a local rejection. */
static inline bool bseed_recovery_probe_begin(bseed_recovery_core_t *core,
                                              uint8_t sequence) {
    if (core->probe_pending)
        return false;

    core->probe_pending  = true;
    core->probe_sequence = sequence;
    return true;
}

/* The adapter must first validate the ZDO frame length and status. */
static inline bool bseed_recovery_probe_answer(bseed_recovery_core_t *core,
                                               uint8_t sequence, uint16_t source,
                                               uint16_t address_of_interest,
                                               bool success) {
    if (!core->probe_pending || sequence != core->probe_sequence ||
        source != 0u || address_of_interest != 0u || !success)
        return false;

    core->probe_pending  = false;
    core->probe_failures = 0u;
    return true;
}

/* A timeout applies only to the currently outstanding transaction. */
static inline void bseed_recovery_probe_timeout(bseed_recovery_core_t *core,
                                                uint32_t now, uint8_t sequence) {
    if (!core->probe_pending || sequence != core->probe_sequence)
        return;

    core->probe_pending = false;
    if (core->probe_failures != 0u &&
        (sequence == core->last_probe_sequence ||
         (uint32_t)(now - core->last_probe_failure_ms) < BSEED_RECOVERY_PROBE_SPACING_MS))
        return;

    core->last_probe_failure_ms = now;
    core->last_probe_sequence   = sequence;
    if (core->probe_failures < 2u)
        core->probe_failures++;
}

static inline bool bseed_recovery_degraded(const bseed_recovery_core_t *core) {
    return core->probe_failures >= 2u;
}

/* Both rungs are secure. Alternate independently of the saturating diagnostic
 * failure counter, so the ladder continues alternating after 255 failures. */
static inline bseed_recovery_rung_t bseed_recovery_rung(
    const bseed_recovery_core_t *core) {
    return core->all_channels_next ? BSEED_RECOVERY_SECURE_ALL_CHANNELS :
           BSEED_RECOVERY_SECURE_CURRENT_CHANNEL;
}

/* Call only when the device owns prior network credentials and neither OTA
 * nor a different commissioning operation owns the radio. Factory-new join
 * remains the platform's normal commissioning path. */
static inline bool bseed_recovery_claim(bseed_recovery_core_t *core,
                                        uint32_t now, bool eligible) {
    if (!eligible || core->pending ||
        (uint32_t)(now - core->scheduled_at_ms) < core->delay_ms)
        return false;

    core->pending_since_ms = now;
    core->generation++;
    if (core->generation == 0u)
        core->generation++;
    core->pending = true;
    return true;
}

static inline void bseed_recovery_finish(bseed_recovery_core_t *core,
                                         uint32_t generation, uint32_t now,
                                         bool succeeded) {
    /* The caller must establish the token from a genuinely correlated
     * completion. Reading core->generation in an uncorrelated SDK callback
     * would not protect a newer operation from an old callback. */
    if (!core->pending || generation == 0u || generation != core->generation)
        return;

    core->pending          = false;
    core->pending_since_ms = 0u;
    if (succeeded) {
        core->failures          = 0u;
        core->all_channels_next = false;
    } else if (core->failures < UINT8_MAX) {
        core->failures++;
    }
    if (!succeeded)
        core->all_channels_next = !core->all_channels_next;

    core->scheduled_at_ms = now;
    core->delay_ms        = bseed_recovery_delay_ms(core->failures, core->stagger_ms);
}

#endif
