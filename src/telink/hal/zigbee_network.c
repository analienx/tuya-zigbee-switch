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
#include "hal/bseed_recovery_core.h"
#include "hal/router_recovery_policy.h"
#include "router_recovery_budget.h"
#include "recovery_wedge_credit.h"
#include "recovery_diagnostics.h"
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
* 0xFFFFFFFF if none | 13 Router best other neighbour cost
* 14 last controlled rescue reason | 15 SDK recovery state at checkpoint */
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
    uint8_t reason = 0u, sdk_state = 0u;
    (void)bseed_recovery_diag_last(&reason, &sdk_state);
    p[14] = reason;
    p[15] = sdk_state;
}

#endif

#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
static bseed_parent_policy_t parent_policy;
static bool     client_floor_active;
static uint32_t client_floor_since_ms;

static void link_floor_restore(void) {
    client_floor_active = false;
    NWK_NEIGHBORTBL_ADD_LQITHRESHOLD = BSEED_PARENT_FLOOR_DEFAULT;
}

static void link_floor_begin(void) {
    client_floor_since_ms            = hal_millis();
    client_floor_active              = true;
    NWK_NEIGHBORTBL_ADD_LQITHRESHOLD = BSEED_PARENT_FLOOR_STRONG;
}

static void link_floor_tick(bool suspended) {
    if (suspended) {
        link_floor_restore();
        return;
    }
    if (!client_floor_active)
        return;

    uint8_t floor = bseed_parent_floor(hal_millis() - client_floor_since_ms);
    NWK_NEIGHBORTBL_ADD_LQITHRESHOLD = floor;
    if (floor == BSEED_PARENT_FLOOR_DEFAULT)
        client_floor_active = false;
}

static bool link_previous_parent_good(void) {
    tl_zb_normal_neighbor_entry_t *parent =
        nwk_neTblGetByShortAddr(zb_getParentShortAddr());

    return parent && bseed_parent_previous_good(link_neighbor_cost(parent));
}

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

/* The pinned BDB callback has NO per-operation token. After an accepted
 * SDK backoff, no second SDK recovery owner may start while the first could
 * still deliver a late completion. telink_zigbee_hal_sdk_owner_rearm_tick()
 * clears these flags only after the owner's terminal callback and a quiet
 * IDLE window; an orphaned owner keeps the budgeted checkpoint path. */
static bool network_sdk_backoff_started_once;
static bool network_sdk_steering_started_once;
#ifdef BSEED_MAINS_CLIENT

/* An accepted ZDO transaction is still SDK-owned even if the application
 * clears a timeout flag. If PARENT_LOST arrives during that transaction,
 * refuse rejoin ownership until a trustworthy terminal result or a bounded
 * processor checkpoint. Do not infer SDK cancellation from a local flag. */
static bool     client_zdo_rejoin_deferred;
static uint32_t client_zdo_rejoin_deferred_since_ms;
#endif

/* Factory-new steering uses untagged BDB terminal callbacks as well. After
 * one accepted steering operation, a second SDK owner cannot be safely
 * assigned within the same boot. A prolonged failed or orphaned commission
 * may instead use the existing fail-closed rescue budget for a clean boot. */
#define FACTORY_STEERING_TERMINAL_HOLD_MS    (45u * 60u * 1000u)
#define FACTORY_STEERING_OWNER_HOLD_MS       (90u * 60u * 1000u)
static bool     network_steering_terminal_seen;
static bool     network_steering_checkpoint_scheduled;
static uint32_t network_steering_terminal_ms;

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
static bool request_rejoin_for_isolated_coordinator(void);

#endif

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
    return bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x434c504fu, CLIENT_RECOVERY_STAGGER_MS);
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
#define ROUTER_PROBE_INTERVAL_MS            (6u * 60u * 1000u)
#define ROUTER_PROBE_DEADLINE_MS            (30u * 1000u)
#define ROUTER_PROBE_FAILURE_THRESHOLD      4u
#define ROUTER_PROBE_REJECT_THRESHOLD       8u
#define ROUTER_REJECT_QUIET_MS              (60u * 60u * 1000u)
#define ROUTER_QUIET_MIN_MS                 (20u * 60u * 1000u)
#define ROUTER_REJOIN_COOLDOWN_MS           (30u * 60u * 1000u)
#define ROUTER_ONE_SHOT_DEADLINE_MS         (10u * 60u * 1000u)
#define ROUTER_ISOLATED_REBOOT_MS           (3u * 60u * 60u * 1000u)
#define ROUTER_INBOUND_GRACE_MS             (90u * 60u * 1000u)
#define ROUTER_MIN_REJOIN_ATTEMPTS          2u
#define ROUTER_ACTIVE_NEIGHBOR_HOLD_MS      (60u * 60u * 1000u)
#define ROUTER_NEIGHBOR_ACTIVITY_MS         (15u * 60u * 1000u)

/* SDK-owned backoff suppresses regular Router ZDO probes. Without a terminal
 * callback JOINED can therefore be stale forever: use a separate maximum
 * operation tenure, with longer grace while this Router serves neighbors.
 * Genuine coordinator replies and authenticated inbound traffic are tracked
 * independently of SDK membership. */
#define ROUTER_COORD_PROOF_FRESH_MS         (20u * 60u * 1000u)
#define ROUTER_BACKOFF_JOINED_QUIET_MS      (3u * 60u * 60u * 1000u)
#define ROUTER_BACKOFF_NEIGHBOR_QUIET_MS    (6u * 60u * 60u * 1000u)
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
static uint32_t router_observation_epoch_ms;
static uint32_t router_recovery_since_ms;
static bool     router_observation_started, router_positive_seen, router_inbound_seen;
static uint32_t router_one_shot_ms;

/* Unlike SDK JOINED, only a verified coordinator reply ends an observed
 * NOT_JOINED outage. Short false-JOINED flaps must not restart this clock. */
static bool     router_not_joined_observed;
static uint32_t router_not_joined_since_ms;

void telink_zigbee_hal_router_note_inbound(void) {
    /* Neighbors can reach this Router even when coordinator 0x0000 cannot.
     * Do not discard a pending coordinator timeout or its failure history. */
    router_last_inbound_ms = hal_millis();
    router_inbound_seen    = true;
}

static void router_coordinator_node_desc_response(void *arg) {
    if (!router_probe_pending || arg == NULL)
        return;

    zdo_zdpDataInd_t *ind = (zdo_zdpDataInd_t *)arg;
    if (ind->zpdu == NULL || ind->length < sizeof(zdo_node_descript_resp_t))
        return;

    zdo_node_descript_resp_t *rsp = (zdo_node_descript_resp_t *)ind->zpdu;

    /* Do not accept replies arriving after the current request deadline.
     * Sequence numbers are only eight bits and can eventually wrap. */
    if ((uint32_t)(hal_millis() - router_last_probe_ms) >=
        ROUTER_PROBE_DEADLINE_MS)
        return;

    if (ind->src_addr == 0x0000u &&
        rsp->seq_num == router_probe_sequence &&
        rsp->status == ZDO_SUCCESS && rsp->nwk_addr_interest == 0x0000u) {
        router_last_positive_ms  = hal_millis();
        router_positive_seen     = true;
        router_recovery_since_ms = router_last_positive_ms;
        bseed_router_rescue_budget_note_good_coordinator(router_last_positive_ms);
        router_probe_failures      = 0;
        router_probe_rejections    = 0;
        router_one_shot_attempts   = 0;
        router_not_joined_observed = false;

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
static bool router_reboot_scheduled;

void telink_zigbee_hal_router_recovery_tick(void) {
    uint32_t now = hal_millis();

    if (!router_observation_started) {
        router_observation_started  = true;
        router_observation_epoch_ms = now;
        router_recovery_since_ms    = now;
    }

    /* Mix all IEEE bytes and separate the probe, rejoin and checkpoint
     * domains. Sequential vendor serials must not cause a fleet-wide burst. */
    uint32_t stagger_ms = bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x52505242u, 120000u);
    uint32_t reboot_stagger_ms = bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x52524254u, 15u * 60u * 1000u);
    uint32_t rejoin_stagger_ms = bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x52524a4eu, 15u * 60u * 1000u);

    /* A lost completion remains an SDK ownership barrier even if the device
     * communicates again. A checkpointed restart clears SDK state; when the
     * ordinary budget is exhausted, accumulate the separate slow-lane credit.
     * Never interrupt OTA or a factory-new commissioning session. */
    bool joined      = zb_isDeviceJoinedNwk();
    bool factory_new = zb_isDeviceFactoryNew();
    bool ota_active  = telink_zigbee_hal_router_ota_in_progress();
    if (factory_new) {
        router_not_joined_observed = false;
    } else if (!joined && !router_not_joined_observed) {
        router_not_joined_observed = true;
        router_not_joined_since_ms = now;
    }

    bool backoff_wedged =
        network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN &&
        (uint32_t)(now - network_last_start_attempt) >=
        ROUTER_RECOVERY_STALL_MS &&
        (uint32_t)(now - router_recovery_since_ms) >=
        ROUTER_RECOVERY_STALL_MS;
    bool recent_coordinator_proof =
        router_positive_seen &&
        (uint32_t)(now - router_last_positive_ms) < ROUTER_COORD_PROOF_FRESH_MS;
    bool recent_inbound = router_inbound_seen &&
                          (uint32_t)(now - router_last_inbound_ms) <
                          ROUTER_INBOUND_GRACE_MS;

    /* No regular ZDO probes run while the SDK owns REJOIN, so requiring
     * four fresh probe failures here would deadlock a falsely JOINED SDK.
     * Limit the fallback to a long, independently timed, no-proof stall. */
    bool backoff_unproven_join =
        backoff_wedged && !recent_coordinator_proof &&
        (uint32_t)(now - network_last_start_attempt) >=
        (recent_inbound ? ROUTER_BACKOFF_NEIGHBOR_QUIET_MS :
         ROUTER_BACKOFF_JOINED_QUIET_MS);
    bool link_unreachable =
        !zb_isDeviceJoinedNwk() ||
        router_probe_failures >= ROUTER_PROBE_FAILURE_THRESHOLD ||
        backoff_unproven_join;
    bool wedge_eligible = (router_one_shot_stalled || backoff_wedged) &&
                          link_unreachable && !factory_new && !ota_active;
    uint32_t wedge_started_ms = router_one_shot_stalled ? router_one_shot_ms :
                                network_last_start_attempt;

    /* A terminal callback does not supply an operation token. Once an SDK
     * operation was accepted, any second operation is quarantined until a
     * checkpoint. Qualify that *separate* state using observed network
     * failure, never by pretending two rejoin attempts actually happened. */
    bool quarantined_no_owner =
        network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        !router_one_shot_active && !router_one_shot_stalled &&
        (router_one_shot_attempted || network_sdk_backoff_started_once ||
         network_sdk_steering_started_once);
    bool coordinator_delivery_failed =
        bseed_router_timeout_evidence(
            router_probe_failures, ROUTER_PROBE_FAILURE_THRESHOLD, now,
            router_positive_seen ? router_last_positive_ms :
            router_observation_epoch_ms,
            ROUTER_QUIET_MIN_MS) ||
        bseed_router_queue_stall_evidence(
            router_probe_rejections, ROUTER_PROBE_REJECT_THRESHOLD, now,
            router_positive_seen ? router_last_positive_ms :
            router_observation_epoch_ms,
            ROUTER_REJECT_QUIET_MS);
    bool quarantine_eligible =
        !factory_new && !ota_active &&
        (joined || router_not_joined_observed) &&
        bseed_router_quarantine_checkpoint_eligible(
            quarantined_no_owner, joined, coordinator_delivery_failed,
            router_reboot_scheduled, now,
            router_positive_seen ? router_last_positive_ms :
            router_observation_epoch_ms,
            router_inbound_seen ? router_last_inbound_ms :
            router_observation_epoch_ms,
            router_not_joined_since_ms,
            ROUTER_ISOLATED_REBOOT_MS + reboot_stagger_ms,
            ROUTER_INBOUND_GRACE_MS,
            ROUTER_RECOVERY_STALL_MS + reboot_stagger_ms);

    /* Exactly one persistent-credit policy update per tick, including when
     * item 70 is exhausted. Healthy coordinator proofs suspend credit. */
    bool slow_ready = bseed_wedge_credit_tick(
        now, wedge_eligible || quarantine_eligible);
    bool stalled_sdk_checkpoint =
        wedge_eligible && !router_reboot_scheduled &&
        (uint32_t)(now - wedge_started_ms) >=
        ROUTER_ONE_SHOT_DEADLINE_MS + reboot_stagger_ms;

    if (stalled_sdk_checkpoint || quarantine_eligible) {
        bseed_budget_result_t result = bseed_router_rescue_budget_try_consume();
        if (result == BSEED_BUDGET_CONSUMED ||
            (result == BSEED_BUDGET_EXHAUSTED && slow_ready &&
             bseed_wedge_credit_consume())) {
            router_reboot_scheduled = true;
            (void)bseed_recovery_diag_record(
                quarantine_eligible ?
                (joined ? BSEED_RESCUE_REASON_ROUTER_ISOLATED :
                 BSEED_RESCUE_REASON_ROUTER_NOT_JOINED) :
                (router_one_shot_stalled ? BSEED_RESCUE_REASON_ROUTER_ORPHAN :
                 BSEED_RESCUE_REASON_ROUTER_BACKOFF),
                (uint8_t)network_recovery_state);
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
            return;
        }
        if (!quarantined_no_owner && !router_reboot_scheduled &&
            (uint32_t)(now - router_recovery_since_ms) >=
            ROUTER_RECOVERY_STALL_MS + reboot_stagger_ms) {
            if (bseed_router_rescue_budget_consume()) {
                router_reboot_scheduled = true;
                printf("Router: stalled rejoin, budgeted checkpoint reboot\r\n");
                (void)bseed_recovery_diag_record(
                    BSEED_RESCUE_REASON_ROUTER_NOT_JOINED,
                    (uint8_t)network_recovery_state);
                schedule_reboot(2000);
            }
        }
        return;
    }

    if (network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        telink_zigbee_hal_router_ota_in_progress()) {
        /* An OTA transfer or SDK backoff owns network operations. Do not
        * overlap ZDO probes or active rejoin while it is working.
        * Do not credit this interval toward 12h verified good health. */
        bseed_router_rescue_budget_note_failure();
        router_probe_pending     = false;
        router_probe_initialized = false;
        return;
    }

    if (!router_probe_initialized) {
        router_probe_initialized = true;
        router_last_probe_ms     = now;
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
        router_positive_seen ? router_last_positive_ms : router_observation_epoch_ms,
        ROUTER_QUIET_MIN_MS);

    /* Queue pressure is not lost routing. Only a much longer series of
     * synchronous request rejections with NO inbound evidence can count as
     * a stuck vendor ZDO subsystem, never as a single missing ACK. */
    bool stuck_request_queue = bseed_router_queue_stall_evidence(
        router_probe_rejections, ROUTER_PROBE_REJECT_THRESHOLD, now,
        router_positive_seen ? router_last_positive_ms : router_observation_epoch_ms,
        ROUTER_REJECT_QUIET_MS);

    /* The BSEED last-resort reset addresses a truly wedged but nominally
     * JOINED Router. A long coordinator outage by itself is insufficient:
     * require both failed rejoin attempts and prolonged absence of *all*
     * incoming application traffic before one checkpointed rescue reboot. */
    if (bseed_router_joined_rescue_eligible(
            router_reboot_scheduled, router_one_shot_active,
            router_one_shot_attempts,
            ROUTER_MIN_REJOIN_ATTEMPTS, now,
            router_positive_seen ? router_last_positive_ms : router_observation_epoch_ms,
            router_inbound_seen ? router_last_inbound_ms : router_observation_epoch_ms,
            ROUTER_ISOLATED_REBOOT_MS + reboot_stagger_ms,
            ROUTER_INBOUND_GRACE_MS,
            repeated_response_timeouts || stuck_request_queue)) {
        if (bseed_router_rescue_budget_consume()) {
            router_reboot_scheduled = true;
            printf("Router: joined isolated, budgeted checkpoint reboot\r\n");
            (void)bseed_recovery_diag_record(
                BSEED_RESCUE_REASON_ROUTER_ISOLATED,
                (uint8_t)network_recovery_state);
            schedule_reboot(2000);
            return;
        }
    }

    if (bseed_router_rejoin_eligible(
            repeated_response_timeouts || stuck_request_queue,
            router_one_shot_stalled, now,
            router_positive_seen ? router_last_positive_ms : router_observation_epoch_ms,
            router_inbound_seen ? router_last_inbound_ms : router_observation_epoch_ms,
            ROUTER_QUIET_MIN_MS, ROUTER_ACTIVE_NEIGHBOR_HOLD_MS,
            ROUTER_NEIGHBOR_ACTIVITY_MS, rejoin_stagger_ms) &&
        !link_router_rejoin_held(now) &&

        /* BDB callbacks cannot identify their SDK operation. A previous
         * backoff or one-shot can deliver a late duplicate after success, so
         * no new owner starts until telink_zigbee_hal_sdk_owner_rearm_tick()
         * has seen it terminate and stay quiet. A later one-shot also keeps
         * the 30-minute cooldown after the previous one. */
        !router_one_shot_attempted &&
        !network_sdk_backoff_started_once &&
        !network_sdk_steering_started_once &&
        (router_one_shot_attempts == 0u ||
         (uint32_t)(now - router_one_shot_ms) >= ROUTER_REJOIN_COOLDOWN_MS)) {
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
        req.nwk_addr_interest = 0x0000u;
        router_last_probe_ms  = now;

        /* Register before calling the SDK, which may complete synchronously.
         * Give it the actual pending sequence storage to fill. */
        router_probe_sequence = 0u;
        router_probe_pending  = true;
        zdo_status_t status = zb_zdoNodeDescReq(
            0x0000u, &req, &router_probe_sequence,
            router_coordinator_node_desc_response);
        if (status == ZDO_SUCCESS) {
            router_probe_rejections = 0;
        } else {
            router_probe_pending = false;
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

#ifdef BSEED_MAINS_CLIENT

/* A parent poll ACK is local evidence only. Probe the coordinator directly
 * and supervise an SDK-owned backoff without releasing its radio ownership. */
#define CLIENT_COORD_PROBE_MS            (10u * 60u * 1000u)
#define CLIENT_COORD_TIMEOUT_MS          (30u * 1000u)
#define CLIENT_BACKOFF_WEDGE_MS          (45u * 60u * 1000u)

/* A healthy parent MAC ACK proves only parent reachability. After repeated
 * independent failed coordinator ZDO probes, start an owned, secure rejoin
 * even when parent polls continue successfully. */
#define CLIENT_COORD_ISOLATION_MS        (20u * 60u * 1000u)
#define CLIENT_COORD_REJOIN_JITTER_MS    (15u * 60u * 1000u)

/* Synchronous ZDO queue rejection is NOT a failed on-air coordinator probe.
 * Only sustained, independently timed refusal after multiple attempts
 * qualifies as a local vendor-ZDO stall. */
#define CLIENT_COORD_REJECT_THRESHOLD    6u
#define CLIENT_COORD_REJECT_QUIET_MS     (60u * 60u * 1000u)
#define CLIENT_COORD_CHECKPOINT_MS       (3u * 60u * 60u * 1000u)
#define CLIENT_BACKOFF_UNPROVEN_MS       (3u * 60u * 60u * 1000u)

/* A PARENT_LOST during an accepted NodeDesc defers the SDK rejoin. That
 * exchange cannot outlive its deadline by minutes, so the deferral ends a
 * short drain grace after it; holding it for hours orphans a Client whose
 * parent vanished mid-probe (likely while degraded: one probe per minute). */
#define CLIENT_ZDO_DRAIN_GRACE_MS        (2u * 60u * 1000u)
static bseed_recovery_core_t client_coord_core;
static bool     client_coord_verified;
static bool     client_coord_started, client_reboot_scheduled;
static uint32_t client_last_probe_ms, client_outage_since_ms;
static uint32_t client_first_rejection_ms;
static uint8_t  client_probe_rejections;
static bool     client_rejections_started, client_not_joined_started;
static uint32_t client_not_joined_since_ms;

static void client_coordinator_node_desc_response(void *arg) {
    if (arg == NULL)
        return;

    zdo_zdpDataInd_t *ind = (zdo_zdpDataInd_t *)arg;
    if (ind->zpdu == NULL || ind->length < sizeof(zdo_node_descript_resp_t))
        return;

    zdo_node_descript_resp_t *rsp = (zdo_node_descript_resp_t *)ind->zpdu;
    if ((uint32_t)(hal_millis() - client_last_probe_ms) >=
        CLIENT_COORD_TIMEOUT_MS)
        return;

    if (bseed_recovery_probe_answer(&client_coord_core, rsp->seq_num,
                                    ind->src_addr, rsp->nwk_addr_interest,
                                    rsp->status == ZDO_SUCCESS)) {
        client_outage_since_ms    = hal_millis();
        client_coord_verified     = true;
        client_probe_rejections   = 0u;
        client_rejections_started = false;

        /* A real matched coordinator reply completes the old ZDO request
         * and cancels the need to start an overlapping SDK rejoin. */
        client_zdo_rejoin_deferred = false;
        bseed_router_rescue_budget_note_good_coordinator(client_outage_since_ms);
    }
}

void telink_zigbee_hal_client_recovery_tick(void) {
    uint32_t now = hal_millis();

    if (!client_coord_started) {
        bseed_recovery_init(&client_coord_core, g_zbMacPib.extAddress, now, false);
        client_coord_started   = true;
        client_last_probe_ms   = now;
        client_outage_since_ms = now;
    }

    /* A previously accepted backoff cannot be started again in this boot.
     * A later genuine NOT_JOINED episode needs its own monotonic clock,
     * independent of the last successful coordinator proof (which may be
     * days old while the device was healthy). */
    if (zb_isDeviceJoinedNwk()) {
        client_not_joined_started = false;
    } else if (!client_not_joined_started) {
        client_not_joined_started  = true;
        client_not_joined_since_ms = now;
    }
    if (zb_isDeviceFactoryNew() || ota_fast_poll_active) {
        if (zb_isDeviceFactoryNew())
            client_zdo_rejoin_deferred = false;

        /* OTA/commissioning suspends both radio actions and qualification:
         * time spent waiting for the image cannot fabricate a queue stall. */
        client_coord_core.probe_pending = false;
        client_probe_rejections         = 0u;
        client_rejections_started       = false;
        (void)bseed_wedge_credit_tick(now, false);
        return;
    }
    if (client_coord_core.probe_pending &&
        (uint32_t)(now - client_last_probe_ms) >= CLIENT_COORD_TIMEOUT_MS) {
        bseed_recovery_probe_timeout(&client_coord_core, now,
                                     client_coord_core.probe_sequence);
        bseed_router_rescue_budget_note_failure();
    }
    if (client_zdo_rejoin_deferred && !client_coord_core.probe_pending &&
        (uint32_t)(now - client_last_probe_ms) >=
        CLIENT_COORD_TIMEOUT_MS + CLIENT_ZDO_DRAIN_GRACE_MS) {
        /* The ordinary paths (app ensure-connectivity while NOT_JOINED,
         * coordinator isolation while JOINED) resume the recovery. */
        client_zdo_rejoin_deferred = false;
    }

    /* The watchdog always runs, even while an SDK rejoin owns the radio.
     * But an outstanding/retrying SDK backoff must NEVER compete with a new
     * coordinator ZDO transmission just because JOINED is stale. */
    if (network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE)
        client_coord_core.probe_pending = false;

    if (network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        !client_zdo_rejoin_deferred &&
        zb_isDeviceJoinedNwk() && !client_coord_core.probe_pending &&
        (uint32_t)(now - client_last_probe_ms) >=
        (client_coord_core.probe_failures ? BSEED_RECOVERY_PROBE_SPACING_MS :
         CLIENT_COORD_PROBE_MS + client_coord_core.stagger_ms)) {
        zdo_node_descriptor_req_t req;
        req.nwk_addr_interest = 0u;
        client_last_probe_ms  = now;

        /* Arm before calling the SDK so synchronous completions can match the
         * sequence written by the SDK through this pointer. Request refusal
         * is a separate local ZDO-queue failure, never an on-air timeout. */
        if (bseed_recovery_probe_begin(&client_coord_core, 0u)) {
            zdo_status_t result = zb_zdoNodeDescReq(
                0u, &req, &client_coord_core.probe_sequence,
                client_coordinator_node_desc_response);
            if (result != ZDO_SUCCESS) {
                client_coord_core.probe_pending = false;
                if (!client_rejections_started) {
                    client_rejections_started = true;
                    client_first_rejection_ms = now;
                }
                if (client_probe_rejections < UINT8_MAX)
                    client_probe_rejections++;
            } else {
                /* Request acceptance is NOT coordinator proof; it merely
                 * ends the consecutive local-queue rejection streak. */
                client_rejections_started = false;
                client_probe_rejections   = 0u;
            }
        }
    }

    bool queue_stalled =
        client_rejections_started &&
        client_probe_rejections >= CLIENT_COORD_REJECT_THRESHOLD &&
        (uint32_t)(now - client_first_rejection_ms) >=
        CLIENT_COORD_REJECT_QUIET_MS &&
        (uint32_t)(now - client_outage_since_ms) >=
        CLIENT_COORD_REJECT_QUIET_MS;
    bool backoff_unproven =
        (uint32_t)(now - network_last_start_attempt) >=
        CLIENT_BACKOFF_UNPROVEN_MS &&
        (uint32_t)(now - client_outage_since_ms) >=
        CLIENT_BACKOFF_UNPROVEN_MS;
    bool link_unreachable =
        !zb_isDeviceJoinedNwk() ||
        bseed_recovery_degraded(&client_coord_core) || queue_stalled ||
        backoff_unproven;

    /* A stale JOINED SDK backoff may coexist with healthy parent service.
     * A queue stall before the SDK claim does not authorize a disruptive
     * reboot after only 45 minutes: require the longer 3-hour independent
     * operation tenure when JOINED still appears true. */
    uint32_t owner_limit = zb_isDeviceJoinedNwk() ?
                           CLIENT_BACKOFF_UNPROVEN_MS : CLIENT_BACKOFF_WEDGE_MS;
    bool wedged = network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN &&
                  link_unreachable &&
                  (uint32_t)(now - network_last_start_attempt) >=
                  owner_limit &&
                  (uint32_t)(now - client_outage_since_ms) >=
                  owner_limit;
    uint32_t stagger = bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x43425254u, 15u * 60u * 1000u);

    /* Do not call wedge_credit_tick(false) followed by tick(true) on each
     * checkpoint cycle: that discards every partial six-hour credit. The
     * persistent clock receives exactly one policy decision per tick. */
    bool checkpoint_after_previous_owner =
        network_sdk_backoff_started_once &&
        network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        (((bseed_recovery_degraded(&client_coord_core) || queue_stalled) &&
          (uint32_t)(now - client_outage_since_ms) >=
          CLIENT_COORD_CHECKPOINT_MS + stagger) ||
         (client_not_joined_started &&
          (uint32_t)(now - client_not_joined_since_ms) >=
          CLIENT_COORD_CHECKPOINT_MS + stagger));
    bool checkpoint_local_queue =
        queue_stalled &&
        network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        (uint32_t)(now - client_first_rejection_ms) >=
        CLIENT_COORD_CHECKPOINT_MS + stagger;
    bool checkpoint_deferred_zdo =
        client_zdo_rejoin_deferred && !client_coord_core.probe_pending &&
        network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        (uint32_t)(now - client_zdo_rejoin_deferred_since_ms) >=
        CLIENT_COORD_CHECKPOINT_MS + stagger;
    bool slow_ready = bseed_wedge_credit_tick(
        now, wedged || checkpoint_after_previous_owner ||
        checkpoint_local_queue || checkpoint_deferred_zdo);
    if (wedged && !client_reboot_scheduled &&
        (uint32_t)(now - network_last_start_attempt) >=
        owner_limit + stagger) {
        bseed_budget_result_t result = bseed_router_rescue_budget_try_consume();
        if (result == BSEED_BUDGET_CONSUMED ||
            (result == BSEED_BUDGET_EXHAUSTED && slow_ready &&
             bseed_wedge_credit_consume())) {
            client_reboot_scheduled = true;
            (void)bseed_recovery_diag_record(
                BSEED_RESCUE_REASON_CLIENT_BACKOFF,
                (uint8_t)network_recovery_state);
            schedule_reboot(2000);
        }
    }

    /* An uncorrelated BDB callback cannot release a new radio owner. An
     * earlier accepted backoff can only be superseded after a checkpoint. */
    if (!client_reboot_scheduled &&
        (checkpoint_after_previous_owner || checkpoint_local_queue ||
         checkpoint_deferred_zdo)) {
        bseed_budget_result_t result = bseed_router_rescue_budget_try_consume();
        if (result == BSEED_BUDGET_CONSUMED ||
            (result == BSEED_BUDGET_EXHAUSTED && slow_ready &&
             bseed_wedge_credit_consume())) {
            client_reboot_scheduled = true;
            (void)bseed_recovery_diag_record(
                queue_stalled ? BSEED_RESCUE_REASON_CLIENT_QUEUE_STALL :
                checkpoint_deferred_zdo ? BSEED_RESCUE_REASON_CLIENT_ZDO_DRAIN :
                BSEED_RESCUE_REASON_CLIENT_BACKOFF,
                (uint8_t)network_recovery_state);
            schedule_reboot(2000);
            return;
        }
    }

    /* Parent MAC confirmations can stay healthy while no route to the
     * coordinator exists. A degraded coordinator probe history is a separate
     * recovery signal; don't wait for four failed parent polls. Serialize
     * through the same SDK backoff owner/cooldown as parent-loss recovery. */
    uint32_t isolation_jitter = bseed_recovery_action_stagger_ms(
        g_zbMacPib.extAddress, 0x4349534fu, CLIENT_COORD_REJOIN_JITTER_MS);
    if (zb_isDeviceJoinedNwk() &&
        network_recovery_state == TELINK_NETWORK_RECOVERY_IDLE &&
        (bseed_recovery_degraded(&client_coord_core) || queue_stalled) &&
        (uint32_t)(now - client_outage_since_ms) >=
        CLIENT_COORD_ISOLATION_MS + isolation_jitter &&
        (!client_health_recovery_attempted ||
         (uint32_t)(now - client_health_last_recovery_ms) >=
         CLIENT_HEALTH_REJOIN_COOLDOWN_MS) &&
        network_start_ready()) {
        client_health_last_recovery_ms   = now;
        client_health_recovery_attempted = true;
        printf("Mains client: coordinator isolation; secure rejoin/backoff\r\n");
        (void)request_rejoin_for_isolated_coordinator();
    }
}

#endif

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

static bool request_rejoin_with_backoff_impl(bool allow_joined,
                                             bool parent_stale) {
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    /* Every caller, not only steering and PARENT_LOST, must respect a
     * pending/orphaned one-shot. BDB callbacks are not operation-tagged. */
    if (router_one_shot_active || router_one_shot_stalled ||
        router_one_shot_attempted || network_sdk_steering_started_once)
        return false;
#endif
    if ((zb_isDeviceJoinedNwk() && !allow_joined) || zb_isDeviceFactoryNew() ||
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        network_sdk_backoff_started_once || !network_start_ready()) {
        return false;
    }
#ifdef BSEED_MAINS_CLIENT
    if (client_coord_core.probe_pending || client_zdo_rejoin_deferred) {
        if (!client_zdo_rejoin_deferred) {
            client_zdo_rejoin_deferred          = true;
            client_zdo_rejoin_deferred_since_ms = hal_millis();
        }
        return false;  /* no SDK-owned rejoin atop an accepted ZDO */
    }
#endif
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    if (router_probe_pending)
        return false;  /* wait for the accepted coordinator ZDO transaction */
#endif

    network_start_attempt();
    firmware_health_increment(&firmware_health.rejoin_start);

    /* Claim before the SDK call: completion may be synchronous. Its native
    * implementation clears JOINED and owns all subsequent retry timers. */
    network_recovery_state           = TELINK_NETWORK_RECOVERY_REJOIN;
    network_sdk_backoff_started_once = true;
#ifdef BSEED_MAINS_CLIENT
    /* A previously armed coordinator probe cannot be allowed to complete
     * after the SDK has acquired the radio for a rejoin. */
    client_coord_core.probe_pending = false;
    zb_rejoinSecModeSet(REJOIN_SECURITY);
#endif
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    router_probe_pending = false;
#endif
#ifdef BSEED_LINK_POLICY
#ifdef BSEED_MAINS_CLIENT
    /* A stale (silent) joined parent is a parent loss for the policy; BDB
     * PARENT_LOST is counted in bdb_commissioning_callback(). */
    if (parent_stale)
        link_note_parent_loss();
    /* Read by the SDK on every attempt of this backoff sequence. */

    /* An isolated coordinator with a responsive parent is NOT a parent
     * loss. Permit this secure rejoin to search for an alternate path
     * without polluting the parent-loss telemetry/policy. */
    PRE_PARENT_FIRST_WHEN_REJOIN =
        (allow_joined && !parent_stale) ? FALSE :
        (link_previous_parent_good() &&
         bseed_parent_policy_prefer_previous(&parent_policy, hal_millis()) ?
         TRUE : FALSE);
    if (!ota_fast_poll_active)
        link_floor_begin();
#else
    /* Not-joined recovery stays sticky; only the verified-failure Router
     * one-shot below rescans. */
    PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
#endif
#endif
    u8 res =
        zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
    if (res != RET_OK) {
#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
        link_floor_restore();
#endif

        /* Some SDKs can emit a terminal callback synchronously. If that
         * callback already changed the owner state, a contradictory return
         * code cannot retroactively prove that no operation existed. */
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN) {
            network_recovery_state           = TELINK_NETWORK_RECOVERY_IDLE;
            network_sdk_backoff_started_once = false;
        }
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Failed to start network rejoin/backoff, status: %d\r\n", res);
        return false;
    }

    printf("Starting network rejoin/backoff\r\n");
    return true;
}

static bool request_rejoin_with_backoff(bool parent_stale) {
    return request_rejoin_with_backoff_impl(parent_stale, parent_stale);
}

#ifdef BSEED_MAINS_CLIENT
static bool request_rejoin_for_isolated_coordinator(void) {
    return request_rejoin_with_backoff_impl(true, false);
}

#endif

static void notify_about_network_status_change() {
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void zdo_leave_indication_callback(nlme_leave_ind_t *pLeaveInd) {
}

void zdo_leave_confirmation_callback(nlme_leave_cnf_t *pLeaveCnf) {
    /* A generic leave indication cannot terminate an uncorrelated SDK
     * owner. Explicit hal_zigbee_leave_network() handles user-requested leave
     * before invoking the SDK; no implicit ownership release here. */
    notify_about_network_status_change();
}

void bdb_init_callback(u8 status, u8 joinedNetwork) {
    /* Boot initialization is not a terminal completion for a separately
     * accepted rejoin/one-shot. A delayed init callback cannot release an
     * SDK owner or license an overlapping radio request. */
    bool sdk_owner_live = network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE;
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    sdk_owner_live = sdk_owner_live || router_one_shot_active ||
                     router_one_shot_stalled;
#endif
    if (sdk_owner_live) {
        printf("BDB init callback during SDK recovery; retain radio owner\r\n");
        return;
    }
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
#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
    /* Failure indications inside SDK backoff also restore admission. Never
     * restart the strong tier from an intermediate failure callback. */
    if (status != BDB_COMMISSION_STA_IN_PROGRESS)
        link_floor_restore();
#endif
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    /* PARENT_LOST may arrive in the middle of an active secure rejoin.
     * Only explicit success or one-shot failure is a terminal boundary.
     * Other BDB indications must not give a second SDK operation ownership. */
    if (router_one_shot_active &&
        ((status == BDB_COMMISSION_STA_SUCCESS &&
          zb_isDeviceJoinedNwk()) ||
         status == BDB_COMMISSION_STA_REJOIN_FAILURE)) {
        /* Only an active one-shot may terminate here. A late uncorrelated
         * callback must not release an orphaned SDK radio operation. */
        router_one_shot_active  = false;
        router_one_shot_stalled = false;
#ifdef BSEED_LINK_POLICY
        PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;
#endif
    }
#endif
    switch (status) {
    case BDB_COMMISSION_STA_SUCCESS:
        /* The pinned SDK marks its joined state before notifying the BDB
         * application on a successful network start. An old untagged SUCCESS
         * arriving during a newer REJOIN must not end that radio owner while
         * this device is still unjoined. Joined=true alone cannot correlate
         * two overlapping SDK epochs; real-radio testing remains required. */
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
        if ((network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN ||
             router_one_shot_active || router_one_shot_stalled) &&
            !zb_isDeviceJoinedNwk()) {
#else
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN &&
            !zb_isDeviceJoinedNwk()) {
#endif
            printf("Unverified BDB SUCCESS; retaining SDK radio owner\r\n");
            break;
        }
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
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_STEERING &&
            network_sdk_steering_started_once &&
            zb_isDeviceFactoryNew()) {
            network_steering_terminal_seen = true;
            network_steering_terminal_ms   = hal_millis();
        }
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

#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
    link_floor_restore();
#endif

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
    if (zb_isDeviceFactoryNew() && network_sdk_steering_started_once) {
        if (network_steering_checkpoint_scheduled)
            return;

        bool ota_busy = false;
#ifdef BSEED_MAINS_CLIENT
        ota_busy = ota_fast_poll_active;
#endif
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
        ota_busy = telink_zigbee_hal_router_ota_in_progress();
#endif
        uint32_t now     = hal_millis();
        uint32_t from_ms = network_steering_terminal_seen ?
                           network_steering_terminal_ms :
                           network_last_start_attempt;
        uint32_t hold_ms = network_steering_terminal_seen ?
                           FACTORY_STEERING_TERMINAL_HOLD_MS :
                           FACTORY_STEERING_OWNER_HOLD_MS;
        uint32_t stagger = bseed_recovery_action_stagger_ms(
            g_zbMacPib.extAddress, 0x46535452u, 15u * 60u * 1000u);

        /* Terminal callbacks cannot be correlated with separate attempts.
         * After a terminal steering outcome, the re-arm tick permits the
         * next attempt after a quiet, staggered delay. Only steering that
         * never reported a terminal outcome reaches this long hold, where a
         * budgeted reboot gives commissioning a fresh SDK owner without
         * leaving/erasing a network or switching the relay. */
        if (!ota_busy &&
            (uint32_t)(now - from_ms) >= hold_ms + stagger &&
            bseed_router_rescue_budget_try_consume() ==
            BSEED_BUDGET_CONSUMED) {
            network_steering_checkpoint_scheduled = true;
            (void)bseed_recovery_diag_record(
                BSEED_RESCUE_REASON_FACTORY_STEERING,
                (uint8_t)network_recovery_state);
            schedule_reboot(2000);
        }
        return;
    }
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

    /* Steering can report completion synchronously. Claim ownership before
     * entering the SDK, then do not overwrite a terminal callback's state. */
    network_recovery_state            = TELINK_NETWORK_RECOVERY_STEERING;
    network_sdk_steering_started_once = true;
    network_steering_terminal_seen    = false;
    u8 res = bdb_networkSteerStart();
    if (res != RET_OK) {
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_STEERING) {
            network_recovery_state            = TELINK_NETWORK_RECOVERY_IDLE;
            network_sdk_steering_started_once = false;
        }
        printf("Failed to start network steering, status: %d\r\n", res);
    }
}

hal_zigbee_status_t hal_zigbee_send_announce(void) {
    if (zb_zdoSendDevAnnance() != RET_OK) {
        return HAL_ZIGBEE_ERR_SEND_FAILED;
    }
    return HAL_ZIGBEE_OK;
}

/* Re-arm SDK recovery ownership after a genuinely finished operation.
 *
 * The pinned BDB callback has no operation token, so no second SDK owner may
 * start while an earlier one could still deliver a callback. That guard must
 * not become "one recovery per boot": the Telink samples rejoin repeatedly in
 * one boot, and a mains Client on a weak parent can lose it many times a day.
 * Without a re-arm, every later parent loss waits hours for a budgeted reboot.
 *
 * An owner is finished only when the SDK's own terminal callback ended it,
 * which the existing callbacks already record: backoff REJOIN -> IDLE (only a
 * terminal callback does that), one-shot active/stalled both cleared, steering
 * terminal_seen. After that the HAL must stay IDLE, with no live owner and no
 * scheduled checkpoint, for a quiet window before the flag is cleared. An
 * orphaned owner (no terminal callback) is never re-armed here; it keeps the
 * wedge/checkpoint path. Routers keep the 30-minute one-shot cooldown. */
#define SDK_OWNER_REARM_QUIET_MS        (5u * 60u * 1000u)
#define SDK_STEERING_REARM_QUIET_MS     (60u * 1000u)
#define SDK_STEERING_REARM_JITTER_MS    (30u * 1000u)

void telink_zigbee_hal_sdk_owner_rearm_tick(void) {
#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
    link_floor_tick(ota_fast_poll_active);
#endif
    static bool     idle_seen;
    static uint32_t idle_since_ms;
    uint32_t        now        = hal_millis();
    bool            owner_live =
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        network_steering_checkpoint_scheduled;

#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    owner_live = owner_live || router_one_shot_active ||
                 router_one_shot_stalled || router_reboot_scheduled;
#endif
#ifdef BSEED_MAINS_CLIENT
    owner_live = owner_live || client_reboot_scheduled;
#endif
    if (owner_live) {
        idle_seen = false;
        return;
    }
    if (!idle_seen) {
        idle_seen     = true;
        idle_since_ms = now;
    }

    uint32_t idle_ms = (uint32_t)(now - idle_since_ms);
    if (network_sdk_backoff_started_once && idle_ms >= SDK_OWNER_REARM_QUIET_MS) {
        network_sdk_backoff_started_once = false;
        printf("SDK backoff finished; recovery re-armed\r\n");
    }
#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    /* The SDK backoff may follow a finished one-shot after the quiet window;
     * a further one-shot keeps its own 30-minute cooldown in the Router tick. */
    if (router_one_shot_attempted && idle_ms >= SDK_OWNER_REARM_QUIET_MS) {
        router_one_shot_attempted = false;
        printf("Router one-shot finished; recovery re-armed\r\n");
    }
#endif
    if (!network_sdk_steering_started_once)
        return;

    if (network_steering_terminal_seen) {
        /* A failed factory-new steering retries like the SDK samples do,
         * after a quiet, IEEE-staggered delay; never a power-cycle wait. */
        uint32_t jitter = bseed_recovery_action_stagger_ms(
            g_zbMacPib.extAddress, 0x53524d41u, SDK_STEERING_REARM_JITTER_MS);
        if (idle_ms >= SDK_STEERING_REARM_QUIET_MS &&
            (uint32_t)(now - network_steering_terminal_ms) >=
            SDK_STEERING_REARM_QUIET_MS + jitter) {
            network_sdk_steering_started_once = false;
            network_steering_terminal_seen    = false;
        }
    } else if (!zb_isDeviceFactoryNew() && idle_ms >= SDK_OWNER_REARM_QUIET_MS) {
        /* Steering succeeded: later recovery uses the ordinary rejoin path. */
        network_sdk_steering_started_once = false;
    }
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
                       parent_policy.rescan_entries,
                       NWK_NEIGHBORTBL_ADD_LQITHRESHOLD, 0u, now);
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

/* Telemetry resumes only on a new coordinator proof, never on JOINED or a
* locally accepted ZDO submission. This does not affect bound controls. */
bool telink_zigbee_hal_hold_coordinator_telemetry(void) {
    static bool     held;
    static uint32_t proof_at_hold;
    bool            failed = false, verified = false;
    uint32_t        proof = 0u;

#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE
    failed = !zb_isDeviceJoinedNwk() || router_probe_failures >= 2u ||
             router_probe_rejections >= ROUTER_PROBE_REJECT_THRESHOLD;
    verified = router_positive_seen;
    proof    = router_last_positive_ms;
#elif defined(BSEED_MAINS_CLIENT)
    failed = !zb_isDeviceJoinedNwk() || bseed_recovery_degraded(&client_coord_core) ||
             client_probe_rejections >= CLIENT_COORD_REJECT_THRESHOLD;
    verified = client_coord_verified;
    proof    = client_outage_since_ms;
#endif
    if (failed && !held) {
        held          = true;
        proof_at_hold = proof;
    } else if (!failed && held && verified && proof != proof_at_hold) {
        held = false;
    }
    return held;
}

void telink_zigbee_hal_network_init(void) {
    zb_init();
#if defined(BSEED_LINK_POLICY) && defined(BSEED_MAINS_CLIENT)
    link_floor_restore();
#endif

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
