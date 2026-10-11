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

#ifdef BSEED_MAINS_CLIENT

/* Rx-on-when-idle clients still need parent MAC-poll keepalives. One per
 * minute is 60x lighter than the standard 1s sleepy-end-device poll. */
#define MAINS_CLIENT_KEEPALIVE_POLL_MS    60000u
#define KEEPALIVE_VERIFY_TICK_MS          60000u
#define KEEPALIVE_RETRY_MS                5000u
#define OTA_POLL_VERIFY_MS                1000u
static hal_task_t keepalive_verify_task;
static bool       ota_fast_poll_active = false;
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

#ifdef BSEED_MAINS_CLIENT

/*
 * The SDK can retain JOINED after the parent ceases to acknowledge MAC polls.
 * In that state the ordinary parent-loss callback/rejoin-backoff never starts,
 * although Zigbee2MQTT cannot route to the socket. Detect actual MAC poll
 * liveness instead of just checking the configured poll rate.
 *
 * This is a non-destructive, one-shot *network rejoin*, not BDB steering,
 * factory reset, radio reset, watchdog reset, or relay manipulation.
 * The SDK guarantees a completion callback for zb_rejoinReq(). The distinct
 * zb_rejoinReqWithBackOff() owns normal parent-loss recovery until success.
 */
#define CLIENT_PARENT_SILENCE_MS            (12u * 60u * 1000u)
#define CLIENT_HEALTH_REJOIN_COOLDOWN_MS    (15u * 60u * 1000u)
#define CLIENT_POLL_FAILURE_THRESHOLD       4u
#define CLIENT_ONE_SHOT_DEADLINE_MS         (10u * 60u * 1000u)

static uint32_t client_health_last_good_ms;
static uint32_t client_health_last_confirm_ms;
static uint32_t client_health_last_probe_ms;
static uint32_t client_health_last_keepalive_refresh_ms;
static uint8_t  client_poll_failure_streak;
static bool     client_health_initialized;
static bool     client_health_probe_active;
static bool     client_health_probe_attempted;

static void client_health_join_epoch(void) {
    uint32_t now = hal_millis();

    client_health_last_good_ms              = now;
    client_health_last_confirm_ms           = now;
    client_health_last_keepalive_refresh_ms = now;
    client_poll_failure_streak              = 0;
    client_health_initialized  = true;
    client_health_probe_active = false;
    /* Preserve last attempt across rejoin to avoid synchronized thrashing. */
}

/* Called only for an authenticated inbound ZCL indication. */
void telink_zigbee_hal_client_note_inbound(void) {
    if (!client_health_initialized)
        return;

    client_health_last_good_ms = hal_millis();
    client_poll_failure_streak = 0;
    client_health_probe_active = false; /* live downlink beats a lost callback */
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

        /* A live parent ACK also closes an orphaned one-shot ownership bit
         * if the vendor omitted a BDB callback after successful recovery. */
        client_health_probe_active = false;
    } else if (client_poll_failure_streak < UINT8_MAX) {
        client_poll_failure_streak++;
    }
}

static void client_health_recovery_tick(void) {
    uint32_t now = hal_millis();

    if (!client_health_initialized || ota_fast_poll_active ||
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        !zb_isDeviceJoinedNwk() || zb_isDeviceFactoryNew()) {
        return;
    }

    /* The one-shot API documents a terminal callback. Still bound ownership
     * in case a broken SDK silently drops it: retain the normal cooldown
     * before attempting another *single* one-shot request. A full SDK-owned
     * rejoin-with-backoff is never forcibly cancelled or overlapped. */
    if (client_health_probe_active) {
        if (now - client_health_last_probe_ms < CLIENT_ONE_SHOT_DEADLINE_MS) {
            return;
        }
        client_health_probe_active = false;
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Mains client: missing one-shot completion; cooldown\r\n");
    }

    /* Healthy quiet devices are normal. No ZCL traffic, or simply no poll
     * callbacks on an RX-on device, cannot prove the parent is broken.
     * A rejoin requires multiple explicit failed MAC confirmations. */
    /* Spread recovery scans across devices after a shared parent outage.
     * IEEE suffix is immutable for this node and requires no NVM writes. */
    uint32_t stagger_ms = (((uint32_t)g_zbMacPib.extAddress[1] << 8) |
                           g_zbMacPib.extAddress[7]) % 120000u;
    uint32_t silence_limit = CLIENT_PARENT_SILENCE_MS + stagger_ms;
    if (now - client_health_last_good_ms < silence_limit) {
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
    if (client_health_probe_attempted &&
        now - client_health_last_probe_ms < CLIENT_HEALTH_REJOIN_COOLDOWN_MS) {
        return;
    }

    /* Set ownership before requesting a one-shot rejoin; a synchronous
     * completion callback must not race the active marker. */
    client_health_last_probe_ms   = now;
    client_health_probe_attempted = true;
    client_health_probe_active    = true;
    firmware_health_increment(&firmware_health.rejoin_start);
    printf("Mains client: parent MAC poll silent; one-shot rejoin\r\n");

    /* No trust-center join window is required: retain the current network
     * key and request the secure rejoin path explicitly. */
    zb_rejoinSecModeSet(REJOIN_SECURITY);
    u8 status = zb_rejoinReq(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
    if (status != RET_OK) {
        client_health_probe_active = false;
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Mains client: one-shot rejoin rejected: %u\r\n", status);
    }
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
        router_last_positive_ms  = hal_millis();
        bseed_router_rescue_budget_note_good_coordinator(router_last_positive_ms);
        router_last_inbound_ms   = router_last_positive_ms;
        router_probe_failures    = 0;
        router_probe_rejections  = 0;
        router_one_shot_attempts = 0;

        /* A valid coordinator response restores liveness, not proof that an
         * unrelated in-flight SDK rejoin is terminal. Keep SDK ownership
         * until its real completion callback to prevent overlapping scans. */
        router_probe_pending = false;
    }
}

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
            router_reboot_scheduled = true;
            if (bseed_router_rescue_budget_consume()) {
                printf("Router: stalled rejoin, budgeted checkpoint reboot\r\n");
                schedule_reboot(2000);
            } else {
                printf("Router: rescue reboot denied by persistent NVM budget\r\n");
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
        router_reboot_scheduled = true;
        if (bseed_router_rescue_budget_consume()) {
            printf("Router: joined isolated, budgeted checkpoint reboot\r\n");
            schedule_reboot(2000);
        } else {
            printf("Router: isolated rescue denied by persistent NVM budget\r\n");
        }
        return;
    }

    if (bseed_router_rejoin_eligible(
            repeated_response_timeouts || stuck_request_queue,
            router_one_shot_stalled, now,
            router_last_positive_ms, router_last_inbound_ms,
            ROUTER_QUIET_MIN_MS, ROUTER_ACTIVE_NEIGHBOR_HOLD_MS,
            ROUTER_NEIGHBOR_ACTIVITY_MS, rejoin_stagger_ms) &&
        (!router_one_shot_attempted ||
         (uint32_t)(now - router_one_shot_ms) >=
         ROUTER_REJOIN_COOLDOWN_MS)) {
        /* Require a verified failure history, never infer link loss merely
         * from the absence of application traffic. No BDB steering, factory
         * reset, counter rollback, channel changes or parent-based logic. */
        router_one_shot_attempted = true;
        router_one_shot_active    = true;
        if (router_one_shot_attempts < UINT8_MAX)
            router_one_shot_attempts++;
        router_one_shot_ms   = now;
        router_probe_pending = false;
        firmware_health_increment(&firmware_health.rejoin_start);
        zb_rejoinSecModeSet(REJOIN_SECURITY);
        u8 status = zb_rejoinReq(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
        if (status != RET_OK) {
            router_one_shot_active = false;
            firmware_health_increment(&firmware_health.rejoin_failure);
            printf("Router: secure one-shot rejoin rejected: %u\r\n", status);
        } else {
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
    if (zb_isDeviceJoinedNwk() || zb_isDeviceFactoryNew() ||
        network_recovery_state != TELINK_NETWORK_RECOVERY_IDLE ||
        !network_start_ready()) {
        return false;
    }

    network_start_attempt();
    firmware_health_increment(&firmware_health.rejoin_start);
    u8 res =
        zb_rejoinReqWithBackOff(zb_apsChannelMaskGet(), g_bdbAttrs.scanDuration);
    if (res != RET_OK) {
        firmware_health_increment(&firmware_health.rejoin_failure);
        printf("Failed to start network rejoin/backoff, status: %d\r\n", res);
        return false;
    }

    network_recovery_state = TELINK_NETWORK_RECOVERY_REJOIN;
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
    }
#endif
#ifdef BSEED_MAINS_CLIENT
    /* A one-shot rejoin has a terminal BDB completion. Do not confuse this
    * with the SDK-owned, ongoing rejoin-with-backoff recovery sequence. */
    if (status != BDB_COMMISSION_STA_IN_PROGRESS)
        client_health_probe_active = false;
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
        network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
        break;
    case BDB_COMMISSION_STA_PARENT_LOST:
        firmware_health_increment(&firmware_health.parent_loss);
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
        // Rejoin success is asynchronous. Synchronize our application-side
        // state from the authoritative stack state so future losses can start
        // a fresh recovery operation.
        if (network_recovery_state == TELINK_NETWORK_RECOVERY_REJOIN)
            firmware_health_increment(&firmware_health.rejoin_success);
        network_recovery_state      = TELINK_NETWORK_RECOVERY_IDLE;
        network_start_retry_pending = false;
        return HAL_ZIGBEE_NETWORK_JOINED;
    }
#ifdef BSEED_MAINS_CLIENT
    if (client_health_probe_active) {
        return HAL_ZIGBEE_NETWORK_JOINING;
    }
#endif
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
        network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
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
#ifdef BSEED_MAINS_CLIENT
        client_health_probe_active ||
#endif
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
