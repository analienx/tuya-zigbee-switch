/* Injected only into the pinned BOARD_ZG204ZL build. Millisecond SDK timers
 * retain elapsed time across low-power sleeps; no raw 32-bit clock timestamps. */
#if BOARD != BOARD_ZG204ZL
#error ZG204ZL recovery overlay must not be built for another board
#endif

static const u32 rejoinBackoffTbl[] = {
    5000, 10000, 20000, 45000, 90000, 180000, 300000, 600000
};
#define REJOIN_BACKOFF_STEPS (sizeof(rejoinBackoffTbl) / sizeof(rejoinBackoffTbl[0]))
static u8 rj_step;
static u8 rj_chan;
static ev_timer_event_t *timerMotionCooldownEvt;
#if !USE_BLE
static bool rj_secMode = REJOIN_SECURITY;
#endif

static void app_saveNwkChannel(void) {
    u8 ch = g_zbMacPib.phyChannelCur;
    if (ch >= 11 && ch <= 26)
        rj_chan = ch;
}

static u32 app_rejoinChannelMask(void) {
    if (rj_chan && (rj_step & 3) != 3)
        return 1UL << rj_chan;
    return zb_apsChannelMaskGet();
}

static bool app_rejoinBusy(void) {
    return !bdb_isIdle() || zb_isUnderRejoinMode() || tl_stackBusy();
}

static s32 sensorDevice_rejoinBackoff(void *arg) {
    (void)arg;
    if (zb_isDeviceFactoryNew() || !g_sensorAppCtx.linkDown) {
        g_sensorAppCtx.timerRejoinBackoffEvt = NULL;
        return -1;
    }
    /* Leave progression and security mode untouched when the SDK is busy or
     * rejects the request. There is still only one owned retry timer. */
    if (app_rejoinBusy())
        return 1000;
#if !USE_BLE
    zb_rejoinSecModeSet(rj_secMode);
#endif
    if (zb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration) != RET_OK)
        return 1000;
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
    if (!g_sensorAppCtx.timerRejoinBackoffEvt)
        g_sensorAppCtx.timerRejoinBackoffEvt = TL_ZB_TIMER_SCHEDULE(sensorDevice_rejoinBackoff, NULL, first_ms);
}

/* Button requests keep the upstream rapid-start behavior. They cannot interrupt
 * an SDK attempt already in progress. Automatic motion uses the path below. */
void app_rejoinNow(void) {
    if (zb_isDeviceFactoryNew() || app_rejoinBusy())
        return;
    g_sensorAppCtx.linkDown = 1;
    rj_step = 0;
    if (g_sensorAppCtx.timerRejoinBackoffEvt)
        TL_ZB_TIMER_CANCEL(&g_sensorAppCtx.timerRejoinBackoffEvt);
    app_rejoinSchedule(100);
}

static s32 app_motionCooldownDone(void *arg) {
    (void)arg;
    timerMotionCooldownEvt = NULL;
    return -1;
}

static void app_motionCooldownClear(void) {
    if (timerMotionCooldownEvt)
        TL_ZB_TIMER_CANCEL(&timerMotionCooldownEvt);
}

void app_rejoinOnMotion(void) {
    if (!g_sensorAppCtx.linkDown || zb_isDeviceFactoryNew() || app_rejoinBusy() || timerMotionCooldownEvt)
        return;
    if (g_sensorAppCtx.timerRejoinBackoffEvt && g_sensorAppCtx.timerRejoinBackoffEvt->timeout <= 100)
        return; /* A request is already due sooner; do not delay it. */
    timerMotionCooldownEvt = TL_ZB_TIMER_SCHEDULE(app_motionCooldownDone, NULL, 60000);
    if (!timerMotionCooldownEvt)
        return; /* Fail bounded if the timer pool is exhausted. */
    if (g_sensorAppCtx.timerRejoinBackoffEvt)
        TL_ZB_TIMER_CANCEL(&g_sensorAppCtx.timerRejoinBackoffEvt);
    /* Crucially, do not reset rj_step or security/channel progression. */
    app_rejoinSchedule(100);
    if (!g_sensorAppCtx.timerRejoinBackoffEvt)
        app_motionCooldownClear();
}
