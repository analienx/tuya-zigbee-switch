/* Injected only into the pinned BOARD_ZG204ZL build. Millisecond SDK timers
 * retain elapsed time across low-power sleeps; no raw 32-bit clock timestamps. */
#if BOARD != BOARD_ZG204ZL
#error ZG204ZL recovery overlay must not be built for another board
#endif

static const u32 rejoinBackoffTbl[] = {
    2000, 3000, 5000, 10000, 20000, 40000, 80000, 300000, 900000, 1800000
};
#define REJOIN_BACKOFF_STEPS (sizeof(rejoinBackoffTbl) / sizeof(rejoinBackoffTbl[0]))
static u8 rj_step;
static u8 rj_chan;
static u8 rejoinDeferralStep;
static u32 pendingRejoinDelayMs;
static u32 lastRejoinAllocSecond;
static ev_timer_event_t *timerMotionCooldownEvt;
static bool motionRetryReady;
#if !USE_BLE
static bool rj_secMode = REJOIN_SECURITY;
#endif

static void app_rememberChannel(u8 ch) {
    if (ch >= 11 && ch <= 26)
        rj_chan = ch;
}

static void app_saveNwkChannel(void) {
    app_rememberChannel(g_zbMacPib.phyChannelCur);
}

static u32 app_rejoinChannelMask(void) {
    /* Pair known-channel attempts, then full-channel attempts. Alternating
     * security can now cover BOTH modes on each mask, including early recovery. */
    if (rj_chan && (rj_step & 3) < 2)
        return 1UL << rj_chan;
    return zb_apsChannelMaskGet();
}

static bool app_rejoinBusy(void) {
    return !bdb_isIdle() || zb_isUnderRejoinMode() || tl_stackBusy();
}

static s32 app_motionCooldownDone(void *arg) {
    (void)arg;
    timerMotionCooldownEvt = NULL;
    motionRetryReady = true;
    return -1;
}

static void app_motionCooldownClear(void) {
    motionRetryReady = false;
    if (timerMotionCooldownEvt)
        TL_ZB_TIMER_CANCEL(&timerMotionCooldownEvt);
}

static void app_motionCooldownStart(void) {
    app_motionCooldownClear();
    timerMotionCooldownEvt = TL_ZB_TIMER_SCHEDULE(app_motionCooldownDone, NULL, 300000);
    /* Allocation failure leaves motion disabled; the owned automatic retry
     * continues and can restore the cooldown on its next accepted request. */
}

static void app_rejoinReset(void) {
    pendingRejoinDelayMs = 0;
    rejoinDeferralStep = 0;
    app_motionCooldownClear();
}

static s32 app_rejoinDefer(void) {
    static const u32 delays[] = {1000, 1000, 1000, 5000, 15000, 60000, 300000};
    u32 delay = delays[rejoinDeferralStep];
    if (rejoinDeferralStep < sizeof(delays) / sizeof(delays[0]) - 1)
        rejoinDeferralStep++;
    return delay;
}

static s32 sensorDevice_rejoinBackoff(void *arg) {
    (void)arg;
    if (zb_isDeviceFactoryNew() || !g_sensorAppCtx.linkDown) {
        g_sensorAppCtx.timerRejoinBackoffEvt = NULL;
        app_rejoinReset();
        return -1;
    }
    /* Leave progression and security mode untouched when the SDK is busy or
     * rejects the request. There is still only one owned retry timer. */
    if (app_rejoinBusy())
        return app_rejoinDefer();
#if !USE_BLE
    zb_rejoinSecModeSet(rj_secMode);
#endif
    if (zb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration) != RET_OK)
        return app_rejoinDefer();
    rejoinDeferralStep = 0;
    app_motionCooldownStart();
#if !USE_BLE
    rj_secMode = !rj_secMode;
#endif
    /* Accepted request N chooses the channel mask before advancing N. */
    u32 delay = rejoinBackoffTbl[(rj_step < REJOIN_BACKOFF_STEPS) ? rj_step : (REJOIN_BACKOFF_STEPS - 1)];
    if (++rj_step == 0)
        rj_step = 0xfc;
    return delay;
}

static void app_rejoinSchedule(u32 first_ms) {
    if (g_sensorAppCtx.timerRejoinBackoffEvt || pendingRejoinDelayMs)
        return;
    g_sensorAppCtx.timerRejoinBackoffEvt = TL_ZB_TIMER_SCHEDULE(sensorDevice_rejoinBackoff, NULL, first_ms);
    if (!g_sensorAppCtx.timerRejoinBackoffEvt) {
        pendingRejoinDelayMs = first_ms;
        lastRejoinAllocSecond = ev_rtc_secondsGet();
    }
}

/* Service a failed allocation on normal app passes. While the SDK timer pool
 * is under pressure, try at most once per RTC second without adding a wakeup. */
void app_rejoinService(void) {
    if (!pendingRejoinDelayMs)
        return;
    if (zb_isDeviceFactoryNew() || !g_sensorAppCtx.linkDown) {
        app_rejoinReset();
        return;
    }
    if (!ev_timer_enough() && ev_rtc_secondsGet() == lastRejoinAllocSecond)
        return;
    u32 delay = pendingRejoinDelayMs;
    pendingRejoinDelayMs = 0;
    app_rejoinSchedule(delay);
}

/* Button requests keep the upstream rapid-start behavior. They cannot interrupt
 * an SDK attempt already in progress. Automatic motion uses the path below. */
void app_rejoinNow(void) {
    if (zb_isDeviceFactoryNew() || app_rejoinBusy())
        return;
    g_sensorAppCtx.linkDown = 1;
    rj_step = 0;
    pendingRejoinDelayMs = 0;
    rejoinDeferralStep = 0;
    if (g_sensorAppCtx.timerRejoinBackoffEvt)
        TL_ZB_TIMER_CANCEL(&g_sensorAppCtx.timerRejoinBackoffEvt);
    app_rejoinSchedule(100);
}

void app_rejoinOnMotion(void) {
    if (!g_sensorAppCtx.linkDown || zb_isDeviceFactoryNew() || app_rejoinBusy() || !motionRetryReady)
        return;
    if (g_sensorAppCtx.timerRejoinBackoffEvt && g_sensorAppCtx.timerRejoinBackoffEvt->timeout <= 100)
        return; /* A request is already due sooner; do not delay it. */
    if (g_sensorAppCtx.timerRejoinBackoffEvt)
        ev_on_timer(g_sensorAppCtx.timerRejoinBackoffEvt, 100);
    else
        app_rejoinSchedule(100);
    /* Crucially, do not reset rj_step or security/channel progression. */
    if (g_sensorAppCtx.timerRejoinBackoffEvt)
        motionRetryReady = false;
}
