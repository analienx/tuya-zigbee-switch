#ifndef BSEED_ROUTER_RECOVERY_POLICY_H
#define BSEED_ROUTER_RECOVERY_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* Small, side-effect-free policy predicates, shared by firmware and the
 * GitHub-hosted native C test harness. The network driver owns all SDK state,
 * requests, timers, rejoin and reboot actions; this policy does NONE of them.
 * All elapsed time comparisons use unsigned subtraction for timer rollover. */
static inline bool bseed_router_timeout_evidence(
    uint8_t failed_probes, uint8_t threshold, uint32_t now,
    uint32_t last_coordinator_reply, uint32_t min_silence_ms) {
    return failed_probes >= threshold &&
           (uint32_t)(now - last_coordinator_reply) >= min_silence_ms;
}

static inline bool bseed_router_queue_stall_evidence(
    uint8_t rejected_requests, uint8_t threshold, uint32_t now,
    uint32_t last_coordinator_reply, uint32_t min_silence_ms) {
    return rejected_requests >= threshold &&
           (uint32_t)(now - last_coordinator_reply) >= min_silence_ms;
}

/* Protect a functioning local mesh when the coordinator route is temporarily
 * broken. A ZDO Node Descriptor round-trip first exercises ordinary NWK route
 * discovery/repair. Successful neighbor ZCL traffic means other Zigbee nodes
 * still depend on this Router; use a longer, deterministic retry window before
 * any potentially disruptive secure rejoin. */
static inline bool bseed_router_rejoin_eligible(
    bool coordinator_path_bad, bool recovery_orphaned,
    uint32_t now, uint32_t last_coordinator_reply, uint32_t last_inbound,
    uint32_t quiet_min_ms, uint32_t serving_neighbors_min_ms,
    uint32_t inbound_active_window_ms, uint32_t stagger_ms) {
    if (!coordinator_path_bad || recovery_orphaned)
        return false;

    bool serving_neighbors =
        (uint32_t)(now - last_inbound) < inbound_active_window_ms;
    uint32_t minimum = serving_neighbors ? serving_neighbors_min_ms :
                       quiet_min_ms;
    return (uint32_t)(now - last_coordinator_reply) >= minimum + stagger_ms;
}

/* A prolonged global coordinator outage cannot, by itself, reboot every
 * router. Require previous active recovery attempts and no inbound ZCL.
 * Caller must also enforce joined, not-factory-new and no OTA conditions. */
static inline bool bseed_router_joined_rescue_eligible(
    bool already_scheduled, bool one_shot_active, uint8_t attempted_rejoins,
    uint8_t min_rejoins, uint32_t now, uint32_t last_coordinator_reply,
    uint32_t last_application_inbound, uint32_t rescue_after_ms,
    uint32_t inbound_grace_ms, bool isolated) {
    return !already_scheduled && !one_shot_active &&
           attempted_rejoins >= min_rejoins &&
           (uint32_t)(now - last_coordinator_reply) >= rescue_after_ms &&
           (uint32_t)(now - last_application_inbound) >= inbound_grace_ms &&
           isolated;
}

#endif
