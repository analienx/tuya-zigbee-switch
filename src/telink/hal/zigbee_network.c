#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "zcl_include.h"
#pragma pack(pop)

#include "ota.h"

#include "telink_size_t_hack.h"

#include "hal/tasks.h"
#include "hal/timer.h"
#include "hal/firmware_health.h"
#include "hal/router_recovery_policy.h"
#include "router_recovery_budget.h"
#include "recovery_wedge_credit.h"
#include "hal/link_policy.h"
#include "device_config/reset.h"
#include "app.h"
#include "hal/zigbee.h"
#include "telink_zigbee_hal.h"
#include "version_cfg.h"

// Forward definitions
void bdb_init_callback(u8 status, u8 joinedNetwork);
void bdb_commissioning_callback(u8 status, void *arg);
void bdb_identify_callback(u8 endpoint, u16 srcAddr, u16 identifyTime);

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd);
void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf);

#ifdef BSEED_LINK_POLICY

/* Basic 0xFF13 link snapshot: ZCL octet string, length byte then 16 bytes.
 * 0 schema | 1 flags (bit0 Router, bit1 Client rescan mode, bit2 link known)
 * 2-3 Client parent / Router next hop to 0x0000 | 4 its LQI | 5 its cost
 * 6 Client parent losses in the last hour / Router failed coordinator probes
 * 7 Client rescan-mode entries / Router route refreshes (saturating)
 * 8 Router rescan rejoins (saturating) | 9-12 ms since last action, or
 * 0xFFFFFFFF if none | 13 Router best other neighbour cost | 14-15 reserved */
uint8_t         firmware_link_snapshot[17] = { 16, 1 };
static uint32_t link_last_action_ms;
static bool     link_action_seen;

/* Link-status outgoing cost when the neighbour reported one, else the SDK's
 * own LQI-to-cost mapping for the last frame heard from it. Same metric for
 * the Client parent and the Router next hop in 0xFF13. */
static __attribute__((unused)) uint8_t link_neighbor_cost(
    const tl_zb_normal_neighbor_entry_t *e) {
    return e->outgoingCost ? e->outgoingCost : rf_lqi2cost(e->lqi);
}

/* Unused in sleepy End Device builds, which have no rescan/refresh action. */
static __attribute__((unused)) void link_note_action(uint32_t now) {
    link_last_action_ms = now;
    link_action_seen    = true;
}

static void link_snapshot_fill(uint8_t flags, uint16_t addr, uint8_t lqi,
                               uint8_t cost, uint8_t recent, uint8_t actions,
                               uint8_t rescans, uint8_t best, uint32_t now) {
    uint8_t *p   = firmware_link_snapshot + 1;
    uint32_t age = link_action_seen ? (uint32_t)(now - link_last_action_ms) :
                   0xFFFFFFFFu;

    p[0]  = 1;
    p[1]  = flags;
    p[2]  = (uint8_t)addr;
    p[3]  = (uint8_t)(addr >> 8);
    p[4]  = lqi;
    p[5]  = cost;
    p[6]  = recent;
    p[7]  = actions;
    p[8]  = rescans;
    p[9]  = (uint8_t)age;
    p[10] = (uint8_t)(age >> 8);
    p[11] = (uint8_t)(age >> 16);
    p[12] = (uint8_t)(age >> 24);
    p[13] = best;
    p[14] = 0;
    p[15] = 0;
}

#endif

#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
static bseed_parent_policy_t parent_policy;

/* Every Client parent-loss signal feeds the rescan policy. */
static void link_note_parent_loss(void) {
    uint32_t now      = hal_millis();
    bool     rescan_0 = parent_policy.rescan;

    if (bseed_parent_policy_note_loss(&parent_policy, now) && !rescan_0) {
        link_note_action(now);
        printf("Client: repeated parent loss; next rejoins rescan\r\n");
    }
}

#endif

#ifdef BSEED_MAINS_CLIENT

/* Rx-on-when-idle clients still need parent MAC-poll keepalives. One per
 * minute is 60x lighter than the standard 1s sleepy-end-device poll. */
#define MAINS_CLIENT_KEEPALIVE_POLL_MS    60000u
#define KEEPALIVE_VERIFY_TICK_MS          60000u
#define KEEPALIVE_RETRY_MS                5000u
#define OTA_POLL_VERIFY_MS                1000u
static hal_task_t keepalive_verify_task;
static bool       ota_fast_poll_active       = false;
static uint32_t   client_health_next_tick_ms = KEEPALIVE_VERIFY_TICK_MS;
static void client_health_recovery_tick(void);

static uint32_t desired_mains_client_poll_ms(void) {
    return ota_fast_poll_active ? RESPONSE_POLL_RATE : MAINS_CLIENT_KEEPALIVE_POLL_MS;
}

static void keepalive_verify_handler(void *arg) {
    uint32_t next_ms = ota_fast_poll_active ? OTA_POLL_VERIFY_MS : KEEPALIVE_VERIFY_TICK_MS;

    (void)arg;
    /* While disconnected the SDK owns recovery polling. */
    if (zb_isDeviceJoinedNwk() &&
        hal_zigbee_get_poll_rate_ms() != desired_mains_client_poll_ms()) {
        if (zb_setPollRate(desired_mains_client_poll_ms()) != RET_OK) {
            next_ms = ota_fast_poll_active ? OTA_POLL_VERIFY_MS : KEEPALIVE_RETRY_MS;
        }
    }
    client_health_recovery_tick();
    if (!ota_fast_poll_active && client_health_next_tick_ms < next_ms) {
        next_ms = client_health_next_tick_ms;
    }
    hal_tasks_schedule(&keepalive_verify_task, next_ms);
}

static void configure_mains_client_keepalive(void) {
    u8 status = zb_isDeviceJoinedNwk() ?
                zb_setPollRate(desired_mains_client_poll_ms()) : RET_OK;

    if (status != RET_OK) {
        printf("Mains client keepalive setup failed: %u\r\n", status);
    }
    keepalive_verify_task.handler = keepalive_verify_handler;
    keepalive_verify_task.arg     = NULL;
    hal_tasks_schedule(&keepalive_verify_task,
                       ota_fast_poll_active ? OTA_POLL_VERIFY_MS :
                       (status == RET_OK ? KEEPALIVE_VERIFY_TICK_MS : KEEPALIVE_RETRY_MS));
}

void hal_zigbee_set_ota_poll_active(bool fast) {
    ota_fast_poll_active = fast;
    // Also retry failed fast-poll setup; a join callback during OTA must not
    // restore the slow keepalive rate while blocks are still being exchanged.
    configure_mains_client_keepalive();
}

#endif

#if defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)
void hal_zigbee_set_ota_poll_active(bool fast) {
    hal_zigbee_set_poll_rate_ms(fast ? RESPONSE_POLL_RATE : POLL_RATE);
}

#endif


typedef enum {
    TELINK_NETWORK_RECOVERY_IDLE = 0,
    TELINK_NETWORK_RECOVERY_STEERING,
    TELINK_NETWORK_RECOVERY_REJOIN,
} telink_network_recovery_state_t;

// Telink's BDB steering and ZDO rejoin/backoff are different recovery paths.
// Keep them mutually exclusive so the application loop cannot start fresh BDB
// commissioning on top of an active rejoin operation.
static telink_network_recovery_state_t network_recovery_state =
    TELINK_NETWORK_RECOVERY_IDLE;

#define NETWORK_START_RETRY_MS    5000u
static bool     network_start_retry_pending;
static uint32_t network_last_start_attempt;

static bool network_start_ready(void) {
    return !network_start_retry_pending ||
           hal_millis() - network_last_start_attempt >= NETWORK_START_RETRY_MS;
}

static void network_start_attempt(void) {
    network_last_start_attempt  = hal_millis();
    network_start_retry_pending = true;
}

static hal_network_status_change_callback_t network_status_change_callback =
    NULL;

static bool request_rejoin_with_backoff(bool parent_stale);

#ifdef BSEED_MAINS_CLIENT

/*
 * The SDK can retain JOINED after the parent ceases to acknowledge MAC polls.
 * In that state the ordinary parent-loss callback/rejoin-backoff never starts,
 * although Zigbee2MQTT cannot route to the socket. Detect actual MAC poll
 * liveness instead of just checking the configured poll rate.
 *
 * Use the same SDK-owned rejoin/backoff as normal parent loss. Unlike a finite
 * one-shot, this operation keeps retrying without an application completion
 * callback. No reset, network erase, or relay manipulation is involved.
 */
#define CLIENT_PARENT_SILENCE_MS            (12u * 60u * 1000u)
#define CLIENT_HEALTH_REJOIN_COOLDOWN_MS    (15u * 60u * 1000u)
#define CLIENT_POLL_FAILURE_THRESHOLD       4u
#define CLIENT_RECOVERY_STAGGER_MS          120000u

static uint32_t client_health_last_good_ms;
static uint32_t client_health_last_confirm_ms;
static uint32_t client_health_last_recovery_ms;
static uint32_t client_health_last_keepalive_refresh_ms;
static uint8_t  client_poll_failure_streak;
static bool     client_health_initialized;
static bool     client_health_recovery_attempted;

static uint32_t client_health_stagger_ms(void) {
    uint32_t hash = 2166136261u;

    for (unsigned i = 0; i < sizeof(g_zbMacPib.extAddress); i++) {
        hash = (hash ^ g_zbMacPib.extAddress[i]) * 16777619u;
    }
    return hash % CLIENT_RECOVERY_STAGGER_MS;
}

static void client_health_join_epoch(void) {
    uint32_t now = hal_millis();

    client_health_last_good_ms              = now;
    client_health_last_confirm_ms           = now;
    client_health_last_keepalive_refresh_ms = now;
    client_poll_failure_streak              = 0;
    client_health_initialized = true;
    /* Preserve last attempt across rejoin to avoid synchronized thrashing. */
}

static void client_health_poll_confirmation(u8 status) {
    uint32_t now = hal_millis();

    if (!client_health_initialized)
        return;

    client_health_last_confirm_ms = now;
    if (status == MAC_SUCCESS || status == MAC_STA_NO_DATA) {
        /* NO_DATA is a successful MAC parent ACK, not a failure. */
        client_health_last_good_ms = now;
        client_poll_failure_streak = 0;
    } else if (client_poll_failure_streak < UINT8_MAX) {
        client_poll_failure_streak++;
    }
}

static void client_health_recovery_tick(void) {
    uint32_t now = hal_millis();

    client_health_next_tick_ms = KEEPALIVE_VERIFY_TICK_MS;
    if (!client_health_initialized || ota_fast_poll_active ||
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        !zb_isDeviceJoinedNwk() || zb_isDeviceFactoryNew()) {
        return;
    }

    /* Healthy quiet devices are normal. No ZCL traffic, or simply no poll
     * callbacks on an RX-on device, cannot prove the parent is broken.
     * A rejoin requires multiple explicit failed MAC confirmations. */
    /* Hash the complete IEEE and preserve the stagger with a shorter final
     * timer interval instead of rounding every scan to a minute boundary. */
    uint32_t silence_limit = CLIENT_PARENT_SILENCE_MS + client_health_stagger_ms();
    uint32_t elapsed       = now - client_health_last_good_ms;
    if (elapsed < silence_limit) {
        uint32_t remaining = silence_limit - elapsed;
        if (client_poll_failure_streak >= CLIENT_POLL_FAILURE_THRESHOLD &&
            remaining < client_health_next_tick_ms) {
            client_health_next_tick_ms = remaining;
        }
        return;
    }
    if (client_poll_failure_streak < CLIENT_POLL_FAILURE_THRESHOLD) {
        /* If the SDK's poll timer stopped producing confirmations, refresh
         * its existing configuration at low frequency; do NOT scan/rejoin
         * a silent but otherwise healthy Client. */
        if (now - client_health_last_confirm_ms >= silence_limit &&
            now - client_health_last_keepalive_refresh_ms >= silence_limit) {
            client_health_last_keepalive_refresh_ms = now;
            (void)zb_setPollRate(desired_mains_client_poll_ms());
            printf("Mains client: no poll confirmations; refresh keepalive\r\n");
        }
        return;
    }
    if (client_health_recovery_attempted &&
        now - client_health_last_recovery_ms < CLIENT_HEALTH_REJOIN_COOLDOWN_MS) {
        return;
    }

    client_health_last_recovery_ms   = now;
    client_health_recovery_attempted = true;
    printf("Mains client: parent MAC poll silent; rejoin/backoff\r\n");
    (void)request_rejoin_with_backoff(true);
}

#endif

#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE

/* A Router is allowed to remain JOINED despite no longer having a usable
 * route to the trust center. Validate real bidirectional reachability with
 * the vendor's supported ZDO Node Descriptor request to coordinator 0x0000.
 * At most one outstanding request, every 6m plus stable IEEE stagger.
 * Four actual request/response failures and 20m without a successful
 * coordinator response are required for one secure, non-destructive rejoin.
 * Inbound application ZCL from another Router is positive local mesh
 * evidence but MUST NOT falsely prove that 0x0000 is reachable. */
#define ROUTER_PROBE_INTERVAL_MS          (6u * 60u * 1000u)
#define ROUTER_PROBE_DEADLINE_MS          (30u * 1000u)
#define ROUTER_PROBE_FAILURE_THRESHOLD    4u
#define ROUTER_PROBE_REJECT_THRESHOLD     8u
#define ROUTER_REJECT_QUIET_MS            (60u * 60u * 1000u)
#define ROUTER_QUIET_MIN_MS               (20u * 60u * 1000u)
#define ROUTER_REJOIN_COOLDOWN_MS         (30u * 60u * 1000u)
#define ROUTER_ONE_SHOT_DEADLINE_MS       (10u * 60u * 1000u)
#define ROUTER_ISOLATED_REBOOT_MS         (3u * 60u * 60u * 1000u)
#define ROUTER_INBOUND_GRACE_MS           (90u * 60u * 1000u)
#define ROUTER_MIN_REJOIN_ATTEMPTS        2u
#define ROUTER_ACTIVE_NEIGHBOR_HOLD_MS    (60u * 60u * 1000u)
#define ROUTER_NEIGHBOR_ACTIVITY_MS       (15u * 60u * 1000u)
static bool     router_probe_initialized;
static bool     router_probe_pending;
static bool     router_one_shot_active;
static bool     router_one_shot_stalled;
static bool     router_one_shot_attempted;
static uint8_t  router_probe_failures;
static uint8_t  router_probe_rejections;
static uint8_t  router_one_shot_attempts;
static uint8_t  router_probe_sequence;
static uint32_t router_last_probe_ms;
static uint32_t router_last_positive_ms; /* only coordinator ZDO replies */
static uint32_t router_last_inbound_ms;  /* any authenticated application ZCL */
static uint32_t router_one_shot_ms;

void telink_zigbee_hal_router_note_inbound(void) {
    if (!router_probe_initialized)
        return;

    /* Neighbors can reach this Router even when coordinator 0x0000 cannot.
     * Do not discard a pending coordinator timeout or its failure history. */
    router_last_inbound_ms = hal_millis();
}

static void router_coordinator_node_desc_response(void *arg) {
    if (!router_probe_pending || arg == NULL)
        return;

    zdo_zdpDataInd_t *ind = (zdo_zdpDataInd_t *)arg;
    if (ind->length < sizeof(zdo_node_descript_resp_t))
        return;

    zdo_node_descript_resp_t *rsp = (zdo_node_descript_resp_t *)ind->zpdu;
    /* Ignore late replies from an earlier ZDO request after its timeout. */
    if (ind->src_addr == 0x0000u &&
        rsp->seq_num == router_probe_sequence &&
        rsp->status == ZDO_SUCCESS && rsp->nwk_addr_interest == 0x0000u) {
        router_last_positive_ms = hal_millis();
        bseed_router_rescue_budget_note_good_coordinator(router_last_positive_ms);
        router_last_inbound_ms   = router_last_positive_ms;
        router_probe_failures    = 0;
        router_probe_rejections  = 0;
        router_one_shot_attempts = 0;

        /* Reachability cannot prove that an orphaned SDK operation has
         * stopped. Keep its guard until a terminal boundary or reboot. */
        router_probe_pending = false;
    }
}

#ifdef BSEED_LINK_POLICY

/* Route-first escalation: before any rejoin, ask the stack to rediscover the
 * route to the coordinator. Sampled once a minute from the SDK's own tables. */
#define LINK_ROUTE_REJOIN_HOLD_MS    (2u * 60u * 1000u)
static bool     route_poor_tracked, route_refreshed, route_sampled, route_known;
static uint32_t route_poor_since_ms, route_last_refresh_ms, route_last_sample_ms;
static uint16_t route_next_hop = 0xFFFFu;
static uint8_t  route_lqi, route_cost, route_best_other;
static uint8_t  route_refreshes, router_rescan_rejoins;

static void link_router_route_tick(uint32_t now) {
    if (route_sampled && (uint32_t)(now - route_last_sample_ms) < 60000u)
        return;

    route_sampled        = true;
    route_last_sample_ms = now;

    /* NWK forwards to a neighbour-table destination directly, so a
     * coordinator in our neighbour table is the next hop whatever the
     * routing table says; only otherwise use the active route entry. */
    uint16_t hop = 0xFFFFu;
    tl_zb_normal_neighbor_entry_t *e = nwk_neTblGetByShortAddr(0x0000u);
    if (e != NULL) {
        hop = 0x0000u;
    } else {
        for (u16 i = 0; i < ROUTING_TABLE_SIZE; i++) {
            const nwk_routingTabEntry_t *r = &g_routingTab[i];
            if (r->dstAddr == 0x0000u && r->status == NWK_ROUTE_STATE_ACTIVE) {
                hop = r->nextHopAddr;
                break;
            }
        }
        e = hop != 0xFFFFu ? nwk_neTblGetByShortAddr(hop) : NULL;
    }

    uint8_t best = 0;
    for (u8 i = 0; i < TL_ZB_NEIGHBOR_TABLE_SIZE; i++) {
        tl_zb_normal_neighbor_entry_t *n = &g_zb_neighborTbl.neighborTbl[i];
        if (!n->used || n == e || n->relationship == NEIGHBOR_IS_CHILD ||
            (n->deviceType != NWK_DEVICE_TYPE_ROUTER &&
             n->deviceType != NWK_DEVICE_TYPE_COORDINATOR))
            continue;
        uint8_t c = link_neighbor_cost(n);
        if (c != 0u && (best == 0u || c < best))
            best = c;
    }

    route_known      = (e != NULL);
    route_next_hop   = route_known ? hop : 0xFFFFu;
    route_lqi        = route_known ? e->lqi : 0u;
    route_cost       = route_known ? link_neighbor_cost(e) : 0u;
    route_best_other = best;
    if (route_known && route_cost >= BSEED_ROUTE_COST_WORST) {
        if (!route_poor_tracked) {
            route_poor_tracked  = true;
            route_poor_since_ms = now;
        }
    } else {
        route_poor_tracked = false;
    }

    if (!bseed_route_refresh_due(route_known, route_cost, route_poor_tracked,
                                 route_poor_since_ms, best,
                                 router_probe_failures, route_refreshed,
                                 route_last_refresh_ms, now))
        return;

    nlme_routeDisc_req_t req;
    TL_SETSTRUCTCONTENT(req, 0);
    req.dstAddr = 0x0000u;

    /* zb_addr_mode_t 2 = 16-bit unicast. Never 0 (ZDP SHORT_ADDR_MODE):
     * mode 0 is a broadcast many-to-one discovery that would turn this
     * Router into a concentrator. */
    req.dstAddrMode       = ZB_ADDR_16BIT_DEV_OR_BROADCAST;
    req.radius            = 0;    /* SDK default radius */
    req.noRouteCache      = FALSE;
    route_refreshed       = true; /* cooldown and rejoin hold also after a rejection */
    route_last_refresh_ms = now;
    if (zb_routeDiscReq(&req) == RET_OK) {
        if (route_refreshes < UINT8_MAX)
            route_refreshes++;
        route_poor_tracked = false;
        link_note_action(now);
        printf("Router: coordinator route rediscovery requested\r\n");
    } else {
        printf("Router: route rediscovery request rejected\r\n");
    }
}

/* A rejoin scan must not start on top of a just-requested route discovery. */
static bool link_router_rejoin_held(uint32_t now) {
    return route_refreshed &&
           (uint32_t)(now - route_last_refresh_ms) < LINK_ROUTE_REJOIN_HOLD_MS;
}

/* The coordinator stayed unreachable through this association for at least
 * 20 min after route refresh: let the one-shot rejoin choose the best parent
 * instead of the previous one. */
static void link_router_rejoin_begin(void) {
    PRE_PARENT_FIRST_WHEN_REJOIN = FALSE;
}

/* Count only requests the SDK accepted. */
static void link_router_rejoin_started(uint32_t now) {
    if (router_rescan_rejoins < UINT8_MAX)
        router_rescan_rejoins++;
    link_note_action(now);
}

/* Back to sticky: rejected request, terminal callback or orphaned one-shot. */
static void link_router_rejoin_end(void) {
    PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
}

#else
/* Policy disabled: no-op hooks keep the recovery tick free of #ifdefs. */
static inline void link_router_route_tick(uint32_t now) {
    (void)now;
}

static inline bool link_router_rejoin_held(uint32_t now) {
    (void)now;
    return false;
}

static inline void link_router_rejoin_begin(void) {
}

static inline void link_router_rejoin_started(uint32_t now) {
    (void)now;
}

static inline void link_router_rejoin_end(void) {
}

#endif

/* Last resort for an SDK rejoin/backoff whose ownership or radio task has
 * stalled beyond the SDK's normal bounded retry windows. This is not an
 * inference from a quiet but still-JOINED Router: such an inference requires
 * independent, affirmative delivery-failure evidence.
 *
 * A controlled reboot retains NVM, network security material, bindings and
 * relay state. It does not leave or factory-reset the network. Spread the
 * delay by the stable IEEE bytes to avoid simultaneous mesh-wide restarts.
 * One rescue maximum per boot; a genuine prolonged coordinator outage can
 * never cause a rapid reset loop. */
#define ROUTER_RECOVERY_STALL_MS    (45u * 60u * 1000u)
static uint32_t router_recovery_since_ms;
static bool     router_recovery_timer_active;
static bool     router_reboot_scheduled;

void telink_zigbee_hal_router_recovery_tick(void) {
    uint32_t now = hal_millis();

    /* Three IEEE bytes spread even a >60-Router mesh across the full
     * jitter windows. A 16-bit suffix can only stagger by ~65 seconds. */
    uint32_t ieee_stagger_seed =
        (((uint32_t)g_zbMacPib.extAddress[1] << 16) |
         ((uint32_t)g_zbMacPib.extAddress[4] << 8) |
         g_zbMacPib.extAddress[7]);
    uint32_t stagger_ms        = ieee_stagger_seed % 120000u;
    uint32_t reboot_stagger_ms = ieee_stagger_seed % (15u * 60u * 1000u);

    /* Rejoin scans interrupt Router service. Spread actual rejoin attempts
    * over 15m, not merely the first 2m of low-impact ZDO health probes. */
    uint32_t rejoin_stagger_ms = reboot_stagger_ms;

    /* A lost completion remains an SDK ownership barrier even if the device
     * communicates again. A checkpointed restart clears SDK state; when the
     * ordinary budget is exhausted, accumulate the separate slow-lane credit.
     * Never interrupt OTA or a factory-new commissioning session. */
    bool wedge_eligible = router_one_shot_stalled &&
                          !zb_isDeviceFactoryNew() &&
                          !telink_zigbee_hal_router_ota_in_progress();
    bool slow_ready = bseed_wedge_credit_tick(now, wedge_eligible);

    if (wedge_eligible && !router_reboot_scheduled &&
        (uint32_t)(now - router_one_shot_ms) >=
        ROUTER_ONE_SHOT_DEADLINE_MS + reboot_stagger_ms) {
        bseed_budget_result_t result = bseed_router_rescue_budget_try_consume();
        if (result == BSEED_BUDGET_CONSUMED ||
            (result == BSEED_BUDGET_EXHAUSTED && slow_ready &&
             bseed_wedge_credit_consume())) {
            router_reboot_scheduled = true;
            schedule_reboot(2000);
            return;
        }
    }

    if (zb_isDeviceFactoryNew() || !zb_isDeviceJoinedNwk()) {
        bseed_router_rescue_budget_note_failure();
        router_probe_initialized = false;
        router_probe_pending     = false;

        /* A one-shot may temporarily leave the network. It still owns the
         * SDK until a terminal BDB callback, even if JOINED goes false.
         * On missing completion, hold a separate orphan guard and let the
         * existing 45-minute not-joined rescue run; never overlap backoff. */
        if (router_one_shot_active &&
            (uint32_t)(now - router_one_shot_ms) >=
            ROUTER_ONE_SHOT_DEADLINE_MS) {
            router_one_shot_active  = false;
            router_one_shot_stalled = true;
            link_router_rejoin_end();
            firmware_health_increment(&firmware_health.rejoin_failure);
            printf("Router: lost one-shot callback while unjoined; isolate SDK\r\n");
        }
        if (zb_isDeviceFactoryNew() ||
            telink_zigbee_hal_router_ota_in_progress()) {
            router_recovery_timer_active = false;
            return;
        }
        if (!router_recovery_timer_active) {
            router_recovery_since_ms     = now;
            router_recovery_timer_active = true;
            return;
        }
        if (!router_reboot_scheduled &&
            (uint32_t)(now - router_recovery_since_ms) >=
            ROUTER_RECOVERY_STALL_MS + reboot_stagger_ms) {
            if (bseed_router_rescue_budget_consume()) {
                router_reboot_scheduled = true;
                printf("Router: stalled rejoin, budgeted checkpoint reboot\r\n");
                schedule_reboot(2000);
            }
        }
        return;
    }

    router_recovery_timer_active = false;
    if (network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        telink_zigbee_hal_router_ota_in_progress()) {
        /* An OTA transfer or SDK backoff owns network operations. Do not
        * overlap ZDO probes or active rejoin while it is working.
        * Do not credit this interval toward 12h verified good health. */
        bseed_router_rescue_budget_note_failure();
        router_probe_pending     = false;
        router_probe_failures    = 0;
        router_probe_rejections  = 0;
        router_probe_initialized = false;
        return;
    }

    if (!router_probe_initialized) {
        router_probe_initialized = true;
        router_last_probe_ms     = now;
        if (!router_one_shot_stalled) {
            router_last_positive_ms  = now;
            router_last_inbound_ms   = now;
            router_one_shot_attempts = 0;
        }
        return;
    }

    if (router_one_shot_active) {
        if ((uint32_t)(now - router_one_shot_ms) <
            ROUTER_ONE_SHOT_DEADLINE_MS)
            return;

        /* Never overlap a request whose completion has not been reported.
         * Keep the cooldown; probes may resume but no second one-shot starts
         * until after it, regardless of the vendor callback behavior. */
        router_one_shot_active  = false;
        router_one_shot_stalled = true;
        link_router_rejoin_end();
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Router: missing one-shot completion; block overlapping recovery\r\n");
    }

    if (router_probe_pending &&
        (uint32_t)(now - router_last_probe_ms) >= ROUTER_PROBE_DEADLINE_MS) {
        router_probe_pending = false;
        bseed_router_rescue_budget_note_failure();
        if (router_probe_failures < UINT8_MAX)
            router_probe_failures++;
    }

    if (!router_one_shot_stalled)
        link_router_route_tick(now);

    bool repeated_response_timeouts = bseed_router_timeout_evidence(
        router_probe_failures, ROUTER_PROBE_FAILURE_THRESHOLD, now,
        router_last_positive_ms, ROUTER_QUIET_MIN_MS);

    /* Queue pressure is not lost routing. Only a much longer series of
     * synchronous request rejections with NO inbound evidence can count as
     * a stuck vendor ZDO subsystem, never as a single missing ACK. */
    bool stuck_request_queue = bseed_router_queue_stall_evidence(
        router_probe_rejections, ROUTER_PROBE_REJECT_THRESHOLD, now,
        router_last_positive_ms, ROUTER_REJECT_QUIET_MS);

    /* The BSEED last-resort reset addresses a truly wedged but nominally
     * JOINED Router. A long coordinator outage by itself is insufficient:
     * require both failed rejoin attempts and prolonged absence of *all*
     * incoming application traffic before one checkpointed rescue reboot. */
    if (bseed_router_joined_rescue_eligible(
            router_reboot_scheduled, router_one_shot_active,
            router_one_shot_stalled ? ROUTER_MIN_REJOIN_ATTEMPTS :
            router_one_shot_attempts,
            ROUTER_MIN_REJOIN_ATTEMPTS, now,
            router_last_positive_ms, router_last_inbound_ms,
            ROUTER_ISOLATED_REBOOT_MS + reboot_stagger_ms,
            ROUTER_INBOUND_GRACE_MS,
            repeated_response_timeouts || stuck_request_queue)) {
        if (bseed_router_rescue_budget_consume()) {
            router_reboot_scheduled = true;
            printf("Router: joined isolated, budgeted checkpoint reboot\r\n");
            schedule_reboot(2000);
            return;
        }
    }

    if (bseed_router_rejoin_eligible(
            repeated_response_timeouts || stuck_request_queue,
            router_one_shot_stalled, now,
            router_last_positive_ms, router_last_inbound_ms,
            ROUTER_QUIET_MIN_MS, ROUTER_ACTIVE_NEIGHBOR_HOLD_MS,
            ROUTER_NEIGHBOR_ACTIVITY_MS, rejoin_stagger_ms) &&
        !link_router_rejoin_held(now) &&
        (!router_one_shot_attempted ||
         (uint32_t)(now - router_one_shot_ms) >=
         ROUTER_REJOIN_COOLDOWN_MS)) {
        /* Require a verified failure history, never infer link loss merely
         * from the absence of application traffic. No BDB steering, factory
         * reset, counter rollback or channel changes. With the link policy
         * the rejoin rescans for the best parent (link_policy.h). */
        router_one_shot_attempted = true;
        router_one_shot_active    = true;
        if (router_one_shot_attempts < UINT8_MAX)
            router_one_shot_attempts++;
        router_one_shot_ms   = now;
        router_probe_pending = false;
        firmware_health_increment(&firmware_health.rejoin_start);
        zb_rejoinSecModeSet(REJOIN_SECURITY);
        link_router_rejoin_begin();
        u8 status = zb_rejoinReq(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
        if (status != RET_OK) {
            link_router_rejoin_end();
            router_one_shot_active = false;
            firmware_health_increment(&firmware_health.rejoin_failure);
            printf("Router: secure one-shot rejoin rejected: %u\r\n", status);
        } else {
            link_router_rejoin_started(now);
            printf("Router: coordinator unreachable; secure one-shot rejoin\r\n");
        }
        return;
    }

    if (!router_probe_pending &&
        (uint32_t)(now - router_last_probe_ms) >=
        ROUTER_PROBE_INTERVAL_MS + stagger_ms) {
        zdo_node_descriptor_req_t req;
        u8 sequence = 0;
        req.nwk_addr_interest = 0x0000u;
        router_last_probe_ms  = now;
        zdo_status_t status = zb_zdoNodeDescReq(0x0000u, &req, &sequence,
                                                router_coordinator_node_desc_response);
        if (status == ZDO_SUCCESS) {
            router_probe_rejections = 0;
            router_probe_sequence   = sequence;
            router_probe_pending    = true;
        } else {
            bseed_router_rescue_budget_note_failure();
            if (router_probe_rejections < UINT8_MAX)
                router_probe_rejections++;
            printf("Router: coordinator ZDO probe rejected: %u\r\n", status);
        }
    }
}

#endif

// Telink ZDO callbacks
static void zdo_sync_confirmation_callback(nlme_sync_cnf_t *confirmation) {
#ifdef BSEED_MAINS_CLIENT
    client_health_poll_confirmation(confirmation->status);
#endif
    firmware_health.last_poll_status = confirmation->status;
    /* An acknowledged poll with no queued data is a healthy parent response. */
    firmware_health_increment((confirmation->status == MAC_SUCCESS ||
                               confirmation->status == MAC_STA_NO_DATA) ?
                              &firmware_health.poll_success :
                              &firmware_health.poll_failure);
}

static void zdo_network_status_callback(zdo_nwk_status_ind_t *indication) {
    hal_telink_nwk_status(indication->shortAddr, indication->status);
}

zdo_appIndCb_t zdo_callbacks = {
    bdb_zdoStartDevCnf,              // start device cnf cb
    NULL,                            // reset cnf cb
    NULL,                            // device announce indication cb
    zdo_leave_indication_callback,   // leave ind cb
    zdo_leave_confirmation_callback, // leave cnf cb
    NULL,                            // nwk update ind cb
    NULL,                            // permit join ind cb
    zdo_sync_confirmation_callback,  // nlme sync cnf cb
    NULL,                            // tc join ind cb
    NULL,                            // tc detects that the frame counter is near limit
    zdo_network_status_callback,     // raw NWK status/address; never initiate recovery here
};

// Telink BDB storage
static bdb_commissionSetting_t bdb_commission_setting = {
    .linkKey.tcLinkKey.keyType = SS_GLOBAL_LINK_KEY,
    .linkKey.tcLinkKey.key     = (u8 *)tcLinkKeyCentralDefault,

    .linkKey.distributeLinkKey.keyType = MASTER_KEY,
    .linkKey.distributeLinkKey.key     = (u8 *)linkKeyDistributedMaster,

    .linkKey.touchLinkKey.keyType = MASTER_KEY,
    .linkKey.touchLinkKey.key     = (u8 *)touchLinkKeyMaster,

    .touchlinkEnable       =                              0,
    .touchlinkChannel      = DEFAULT_CHANNEL,
    .touchlinkLqiThreshold =                           0xA0,
};

static bdb_appCb_t device_bdb_cb = {
    bdb_init_callback,
    bdb_commissioning_callback,
    bdb_identify_callback,
    NULL,
};

static bool start_rejoin_with_backoff(void) {
    return request_rejoin_with_backoff(false);
}

static bool request_rejoin_with_backoff(bool parent_stale) {
    if ((zb_isDeviceJoinedNwk() && !parent_stale) || zb_isDeviceFactoryNew() ||
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        !network_start_ready()) {
        return false;
    }

    network_start_attempt();
    firmware_health_increment(&firmware_health.rejoin_start);

    /* Claim before the SDK call: completion may be synchronous. Its native
    * implementation clears JOINED and owns all subsequent retry timers. */
    network_recovery_state = TELINK_NETWORK_RECOVERY_REJOIN;
#ifdef BSEED_MAINS_CLIENT
    zb_rejoinSecModeSet(REJOIN_SECURITY);
#endif
#ifdef BSEED_LINK_POLICY
#ifdef BSEED_MAINS_CLIENT
    /* A stale (silent) joined parent is a parent loss for the policy; BDB
     * PARENT_LOST is counted in bdb_commissioning_callback(). */
    if (parent_stale)
        link_note_parent_loss();
    /* Read by the SDK on every attempt of this backoff sequence. */
    PRE_PARENT_FIRST_WHEN_REJOIN =
        bseed_parent_policy_prefer_previous(&parent_policy, hal_millis()) ?
        TRUE : FALSE;
#else
    /* Not-joined recovery stays sticky; only the verified-failure Router
     * one-shot below rescans. */
    PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
#endif
#endif
    u8 res =
        zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
    if (res != RET_OK) {
        network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Failed to start network rejoin/backoff, status: %d\r\n", res);
        return false;
    }

    printf("Starting network rejoin/backoff\r\n");
    return true;
}

static void notify_about_network_status_change() {
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd) {
}

void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf) {
    network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
    notify_about_network_status_change();
}

void bdb_init_callback(u8 status, u8 joinedNetwork) {
    if (status == BDB_INIT_STATUS_SUCCESS) {
        network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE;
        network_start_retry_pending = false;
        if (joinedNetwork) {
#ifdef BSEED_MAINS_CLIENT
            client_health_join_epoch();
            configure_mains_client_keepalive();
#endif
#if defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)
            zb_setPollRate(POLL_RATE);
#endif
#ifdef BSEED_PM_B28WRPVX
            telink_zigbee_hal_request_ota_query();
#else
            ota_queryStart(OTA_QUERY_INTERVAL);
#endif
        }
    } else {
        network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
        if (joinedNetwork) {
            (void)start_rejoin_with_backoff();
        }
    }
    notify_about_network_status_change();
}

void bdb_commissioning_callback(u8 status, void *arg) {
    printf("BDB commissioning callback, status: %d\r\n", status);
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    /* PARENT_LOST may arrive in the middle of an active secure rejoin.
     * Only explicit success or one-shot failure is a terminal boundary.
     * Other BDB indications must not give a second SDK operation ownership. */
    if (status == BDB_COMMISSION_STA_SUCCESS ||
        status == BDB_COMMISSION_STA_REJOIN_FAILURE) {
        router_one_shot_active  = false;
        router_one_shot_stalled = false;
#ifdef BSEED_LINK_POLICY
        PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
#endif
    }
#endif
    switch (status) {
    case BDB_COMMISSION_STA_SUCCESS:
        /* A Router can complete a secure rejoin while zb_isDeviceJoinedNwk()
         * never became false in the main loop. Do not miss this join epoch. */
        app_rearm_network_announcement();
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN)
            firmware_health_increment(&firmware_health.rejoin_success);
        network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE;
        network_start_retry_pending = false;
#ifdef BSEED_MAINS_CLIENT
        client_health_join_epoch();
        configure_mains_client_keepalive();
#endif
#if defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)
        // Need set poll rate manually,
        // to avoid bugs related to no poll task
        // after fast re-connect.
        zb_setPollRate(POLL_RATE);
        printf("Set poll rate to %d\r\n", POLL_RATE);
#endif
#ifdef BSEED_PM_B28WRPVX
        telink_zigbee_hal_request_ota_query();
#else
        ota_queryStart(OTA_QUERY_INTERVAL);
#endif
        break;
    case BDB_COMMISSION_STA_IN_PROGRESS:
        break;
    case BDB_COMMISSION_STA_NOT_AA_CAPABLE:
    case BDB_COMMISSION_STA_NO_NETWORK:
    case BDB_COMMISSION_STA_TCLK_EX_FAILURE:
    case BDB_COMMISSION_STA_TARGET_FAILURE:
    case BDB_COMMISSION_STA_FORMATION_FAILURE:
    case BDB_COMMISSION_STA_NO_IDENTIFY_QUERY_RESPONSE:
    case BDB_COMMISSION_STA_BINDING_TABLE_FULL:
    case BDB_COMMISSION_STA_NOT_PERMITTED:
    case BDB_COMMISSION_STA_NO_SCAN_RESPONSE:
        // These are terminal outcomes for the current BDB attempt. In
        // particular, NO_SCAN_RESPONSE on a factory-new device must return to
        // normal steering rather than pretending there is an old network to
        // rejoin. The next application tick chooses steering vs rejoin from
        // zb_isDeviceFactoryNew().

        /* These commissioning outcomes do not terminate an SDK-owned backoff
         * sequence. It remains responsible until joined state or success. */
        if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN) {
            network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
        }
        break;
    case BDB_COMMISSION_STA_PARENT_LOST:
        firmware_health_increment(&firmware_health.parent_loss);
#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
        /* Count only a loss that starts a new recovery. PARENT_LOST raised
         * by the SDK's own ongoing backoff is the same outage, not another
         * loss; counting it would turn one parent reboot into rescan mode. */
        if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN)
            link_note_parent_loss();
#endif
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
        /* Never start SDK-owned backoff atop an ongoing/orphaned one-shot.
         * A Router can receive this nonterminal indication mid-rejoin. */
        if (router_one_shot_active || router_one_shot_stalled)
            break;
#endif
        // If the SDK's self-sustaining rejoin/backoff is already active, keep
        // ownership with that operation. Do not clear the state and create a
        // second backoff instance from a callback generated by the first one.
        if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN) {
            network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
            (void)start_rejoin_with_backoff();
        }
        break;
    case BDB_COMMISSION_STA_REJOIN_FAILURE:
        firmware_health_increment(&firmware_health.rejoin_failure);
        // zb_rejoinReqWithBackOff() is documented to continue its backoff
        // sequence until rejoin success. Intermediate failures therefore do
        // not terminate our REJOIN state or start another recovery sequence.
        if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN) {
            network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
            if (!zb_isDeviceFactoryNew()) {
                (void)start_rejoin_with_backoff();
            }
        }
        break;
    default:
        break;
    }
    notify_about_network_status_change();
}

void bdb_identify_callback(u8 endpoint, u16 srcAddr, u16 identifyTime) {
    // Not implemented
}

hal_zigbee_network_status_t hal_zigbee_get_network_status(void) {
    if (zb_isDeviceJoinedNwk()) {
        /* Membership is not a terminal callback for an outstanding SDK
         * operation. Report the stack state without surrendering ownership. */
        return HAL_ZIGBEE_NETWORK_JOINED;
    }
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    if (router_one_shot_active || router_one_shot_stalled)
        return HAL_ZIGBEE_NETWORK_JOINING;
#endif
    if (network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE) {
        return HAL_ZIGBEE_NETWORK_JOINING;
    }
    return HAL_ZIGBEE_NETWORK_NOT_JOINED;
}

void hal_register_on_network_status_change_callback(
    hal_network_status_change_callback_t callback) {
    network_status_change_callback = callback;
    notify_about_network_status_change();
}

void hal_zigbee_leave_network(void) {
    nlme_leave_req_t leaveReq;

    TL_SETSTRUCTCONTENT(leaveReq, 0);
    leaveReq.removeChildren     = 1;
    leaveReq.rejoin             = 0;
    network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE;
    network_start_retry_pending = false;
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    router_one_shot_active  = false;
    router_one_shot_stalled = false;
#endif
    zb_nlmeLeaveReq(&leaveReq);
    notify_about_network_status_change();
}

void hal_zigbee_start_network_steering(void) {
    if (zb_isDeviceJoinedNwk()) {
        return;
    }

#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    if (router_one_shot_active || router_one_shot_stalled)
        return;
#endif
    // The public HAL name is historical. On Telink this entry point now means
    // "ensure connectivity": factory-new devices perform BDB steering, while
    // devices that already own network state use the SDK's rejoin/backoff
    // recovery. Never let the two operations overlap.
    if (network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        !network_start_ready()) {
        return;
    }

    if (!zb_isDeviceFactoryNew()) {
        (void)start_rejoin_with_backoff();
        return;
    }

    printf("Starting network steering\r\n");
    network_start_attempt();
    u8 res = bdb_networkSteerStart();
    if (res == 0) {
        network_recovery_state = TELINK_NETWORK_RECOVERY_STEERING;
    } else {
        printf("Failed to start network steering, status: %d\r\n", res);
    }
}

hal_zigbee_status_t hal_zigbee_send_announce(void) {
    if (zb_zdoSendDevAnnance() != RET_OK) {
        return HAL_ZIGBEE_ERR_SEND_FAILED;
    }
    return HAL_ZIGBEE_OK;
}

void hal_zigbee_set_poll_rate_ms(uint32_t poll_rate_ms) {
    zb_setPollRate(poll_rate_ms);
}

uint32_t hal_zigbee_get_poll_rate_ms(void) {
    return zb_getPollRate();
}

#ifdef BSEED_LINK_POLICY
/* Refresh Basic 0xFF13 once per second. Read-only towards the SDK. */
void telink_zigbee_hal_link_snapshot_update(void) {
    static uint32_t last_ms;
    static bool     started;
    uint32_t        now = hal_millis();

    if (started && (uint32_t)(now - last_ms) < 1000u)
        return;

    started = true;
    last_ms = now;
#if defined(BSEED_MAINS_CLIENT)
    uint16_t parent = zb_isDeviceJoinedNwk() ? zb_getParentShortAddr() : 0xFFFFu;
    tl_zb_normal_neighbor_entry_t *e =
        parent != 0xFFFFu ? nwk_neTblGetByShortAddr(parent) : NULL;
    bseed_parent_policy_expire(&parent_policy, now);
    link_snapshot_fill((uint8_t)((parent_policy.rescan ? 2u : 0u) | (e ? 4u : 0u)),
                       parent, e ? e->lqi : 0u, e ? link_neighbor_cost(e) : 0u,
                       bseed_parent_losses_recent(&parent_policy, now),
                       parent_policy.rescan_entries, 0u, 0u, now);
#elif ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    link_snapshot_fill((uint8_t)(1u | (route_known ? 4u : 0u)), route_next_hop,
                       route_lqi, route_cost, router_probe_failures,
                       route_refreshes, router_rescan_rejoins, route_best_other,
                       now);
#else
    link_snapshot_fill(0u, 0xFFFFu, 0u, 0u, 0u, 0u, 0u, 0u, now);
#endif
}

#endif

// Internal interface functions

void telink_zigbee_hal_network_init(void) {
    zb_init();

#if ZB_ROUTER_ROLE
    /* Zigbee R23 section 3.6.10 requires a parent that accepts End Device
     * Timeout requests to advertise at least one supported keepalive method.
     * Telink 3.7.2.0 initializes nwkParentInformation to zero even though its
     * router stack handles MAC data polls and child timeout refresh. Advertise
     * only the mechanism proven by this stack so sleepy children such as IKEA
     * RODRET can use their normal MAC polls as parent keepalives. */
    g_zbNIB.parentInfo = MAC_DATA_POLL_KEEPALIVE_BIT;
#endif

#ifdef BSEED_LINK_POLICY
    /* Sticky by default; link_policy.h decides per rejoin when to rescan. */
    PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
#endif

    zb_zdoCbRegister(&zdo_callbacks);
#if ZB_ROUTER_ROLE || defined(BSEED_MAINS_CLIENT)
    // A mains client is a Zigbee End Device only in the topology sense. Keep
    // the radio on continuously, advertise mains power, and do not enable the
    // sleepy-end-device polling model. ZB_MAC_RX_ON_WHEN_IDLE=1 also seeds the
    // Telink MAC PIB at stack initialization; update the descriptor explicitly
    // here so the role invariant remains obvious and testable.
    af_nodeDescRxOnWhenIdleUpdate(1);
    power_descriptor_t mains_power = {
        .current_power_mode         = POWER_MODE_RECEIVER_SYNCHRONIZED_WHEN_ON_IDLE,
        .available_power_sources    = POWER_SRC_MAINS_POWER,
        .current_power_source       = POWER_SRC_MAINS_POWER,
        .current_power_source_level = POWER_LEVEL_PERCENT_100,
    };
    af_powerDescriptorSet(&mains_power);
#else
    af_powerDescPowerModeUpdate(POWER_MODE_RECEIVER_COMES_PERIODICALLY);
#endif
}

void telink_zigbee_hal_bdb_init(af_simple_descriptor_t *endpoint_descriptor) {
    // BDB init
    // TODO: Support from restore from deep sleep here
    bdb_init(endpoint_descriptor, &bdb_commission_setting, &device_bdb_cb, 1);
}
