#ifndef BSEED_LINK_POLICY_H
#define BSEED_LINK_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* Side-effect-free parent and route selection policy, shared by the Telink
 * network driver and the GitHub-hosted native C tests. The driver owns every
 * SDK request; these predicates only decide. Elapsed time always uses
 * unsigned subtraction so millisecond timer rollover is harmless.
 *
 * Principles: stay with the current parent/route by default; move only on
 * repeated measured failure; least disruptive action first; hysteresis.
 * Costs are the Telink SDK's own path costs (1 best .. 7 worst; on TLSR8258
 * LQI <= 45, about -84 dBm, is already cost 7), never raw LQI thresholds. */

/* ---- Client: when to stop preferring the previous parent on rejoin ---- */

#define BSEED_PARENT_LOSS_TRIGGER       3u
#define BSEED_PARENT_LOSS_WINDOW_MS     (60u * 60u * 1000u)
#define BSEED_PARENT_STABLE_MS          (6u * 60u * 60u * 1000u)

/* Client-only admission floor, scoped to the start of a recovery sequence.
 * These are elapsed-time windows, not promises about SDK scan cadence.
 * The ordinary SDK floor is restored after 90s even without a callback. */
#define BSEED_PARENT_FLOOR_DEFAULT      69u
#define BSEED_PARENT_FLOOR_MEDIUM       94u
#define BSEED_PARENT_FLOOR_STRONG       118u
#define BSEED_PARENT_FLOOR_STRONG_MS    30000u
#define BSEED_PARENT_FLOOR_MEDIUM_MS    90000u

static inline uint8_t bseed_parent_floor(uint32_t elapsed_ms) {
    if (elapsed_ms < BSEED_PARENT_FLOOR_STRONG_MS)
        return BSEED_PARENT_FLOOR_STRONG;

    if (elapsed_ms < BSEED_PARENT_FLOOR_MEDIUM_MS)
        return BSEED_PARENT_FLOOR_MEDIUM;

    return BSEED_PARENT_FLOOR_DEFAULT;
}

static inline bool bseed_parent_previous_good(uint8_t cost) {
    return cost != 0u && cost <= 2u;
}

typedef struct {
    uint32_t loss_ms[BSEED_PARENT_LOSS_TRIGGER]; /* ring of recent losses */
    uint8_t  loss_count;                         /* valid ring entries */
    uint8_t  loss_next;
    bool     rescan;                             /* next rejoins rescan */
    uint32_t last_loss_ms;
    uint32_t rescan_since_ms;
    uint8_t  rescan_entries;                     /* saturating diagnostic */
} bseed_parent_policy_t;

static inline uint8_t bseed_parent_losses_recent(
    const bseed_parent_policy_t *p, uint32_t now) {
    uint8_t recent = 0;

    for (uint8_t i = 0; i < p->loss_count; i++) {
        if ((uint32_t)(now - p->loss_ms[i]) < BSEED_PARENT_LOSS_WINDOW_MS)
            recent++;
    }
    return recent;
}

/* Six hours without a parent loss ends rescan mode. Evaluated BEFORE a new
 * loss is recorded: recording first would reset the stable timer and keep a
 * Client in rescan mode until reboot. */
static inline void bseed_parent_policy_expire(bseed_parent_policy_t *p,
                                              uint32_t now) {
    if (p->rescan &&
        (uint32_t)(now - p->last_loss_ms) >= BSEED_PARENT_STABLE_MS) {
        p->rescan     = false;
        p->loss_count = 0;
        p->loss_next  = 0;
    }
}

/* Record one parent loss. Entering rescan mode needs three losses within an
 * hour: a single blip (parent reboot, brief interference) keeps the fast
 * previous-parent rejoin. Returns true while in rescan mode. */
static inline bool bseed_parent_policy_note_loss(bseed_parent_policy_t *p,
                                                 uint32_t now) {
    bseed_parent_policy_expire(p, now);
    p->loss_ms[p->loss_next] = now;
    p->loss_next             = (uint8_t)((p->loss_next + 1u) % BSEED_PARENT_LOSS_TRIGGER);
    if (p->loss_count < BSEED_PARENT_LOSS_TRIGGER)
        p->loss_count++;
    p->last_loss_ms = now;
    if (!p->rescan &&
        bseed_parent_losses_recent(p, now) >= BSEED_PARENT_LOSS_TRIGGER) {
        p->rescan          = true;
        p->rescan_since_ms = now;
        if (p->rescan_entries < UINT8_MAX)
            p->rescan_entries++;
    }
    return p->rescan;
}

/* Value for the SDK's PRE_PARENT_FIRST_WHEN_REJOIN at the moment a rejoin
 * starts. Six hours without a parent loss returns to sticky behaviour. */
static inline bool bseed_parent_policy_prefer_previous(bseed_parent_policy_t *p,
                                                       uint32_t now) {
    bseed_parent_policy_expire(p, now);
    return !p->rescan;
}

/* ---- Router: when to request a non-disruptive route rediscovery ---- */

#define BSEED_ROUTE_COST_WORST             7u
#define BSEED_ROUTE_COST_GOOD              3u
#define BSEED_ROUTE_POOR_HOLD_MS           (15u * 60u * 1000u)
#define BSEED_ROUTE_REFRESH_COOLDOWN_MS    (60u * 60u * 1000u)
#define BSEED_ROUTE_REACTIVE_FAILURES      2u

/* Reactive: two failed coordinator probes. Proactive: the next hop toward
 * the coordinator has been at the worst cost for 15 minutes while another
 * router neighbour is at a good cost. Either way at most once per hour.
 * A rediscovery keeps the device joined and its children attached; rejoin
 * and reboot remain the separate, later escalation steps. */
static inline bool bseed_route_refresh_due(
    bool has_route, uint8_t next_hop_cost, bool poor_tracked,
    uint32_t poor_since_ms, uint8_t best_other_cost, uint8_t probe_failures,
    bool refreshed_before, uint32_t last_refresh_ms, uint32_t now) {
    if (refreshed_before &&
        (uint32_t)(now - last_refresh_ms) < BSEED_ROUTE_REFRESH_COOLDOWN_MS)
        return false;

    if (probe_failures >= BSEED_ROUTE_REACTIVE_FAILURES)
        return true;

    return has_route && next_hop_cost >= BSEED_ROUTE_COST_WORST &&
           poor_tracked &&
           (uint32_t)(now - poor_since_ms) >= BSEED_ROUTE_POOR_HOLD_MS &&
           best_other_cost != 0u && best_other_cost <= BSEED_ROUTE_COST_GOOD;
}

#endif
