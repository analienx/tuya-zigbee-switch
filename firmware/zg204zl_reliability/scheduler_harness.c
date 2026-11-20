/* Host fixture for the exact C section injected into zb_appCb.c. This models
 * SDK timer ownership and asynchronous request acceptance, not RF behavior. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
#define BOARD 50
#define BOARD_ZG204ZL 50
#define USE_BLE 0
#define TRUE true
#define FALSE false
#define REJOIN_SECURITY true
#define RET_OK 0
#define sws_printf(...)
#define sws_puts(...)
typedef struct ev_timer_event_t {
    u32 timeout;
    u32 period;
    s32 (*cb)(void *);
    void *data;
    bool active;
} ev_timer_event_t;
static struct { bool linkDown; ev_timer_event_t *timerRejoinBackoffEvt; } g_sensorAppCtx;
static struct { u8 phyChannelCur; } g_zbMacPib = {11};
static struct { u8 scanDuration; } g_bdbAttrs = {3};
static ev_timer_event_t timers[8];
static uint64_t now_ms, inflight_until;
static bool factory_new, forced_busy, bdb_busy, stack_busy, no_timers;
static u8 request_status;
static bool selected_security;
static unsigned requests, full_scans, secure_full_scans, insecure_full_scans;
static u32 last_mask;
static bool zb_isDeviceFactoryNew(void) { return factory_new; }
static bool zb_isUnderRejoinMode(void) { return forced_busy || now_ms < inflight_until; }
static bool bdb_isIdle(void) { return !bdb_busy; }
static bool tl_stackBusy(void) { return stack_busy; }
static u32 zb_apsChannelMaskGet(void) { return 0x07fff800UL; }
static void zb_rejoinSecModeSet(bool secure) { selected_security = secure; }
static u8 zb_rejoinReq(u32 mask, u8 duration) {
    assert(!zb_isUnderRejoinMode() && bdb_isIdle() && !tl_stackBusy());
    assert(duration == 3);
    if (request_status != RET_OK) return request_status;
    requests++;
    last_mask = mask;
    full_scans += mask == zb_apsChannelMaskGet();
    if (mask == zb_apsChannelMaskGet()) {
        secure_full_scans += selected_security;
        insecure_full_scans += !selected_security;
    }
    inflight_until = now_ms + 2000;
    return RET_OK;
}
static ev_timer_event_t *schedule(s32 (*cb)(void *), void *data, u32 timeout) {
    if (no_timers) return NULL;
    for (unsigned i = 0; i < 8; i++) if (!timers[i].active) {
        timers[i] = (ev_timer_event_t){timeout, timeout, cb, data, true};
        return &timers[i];
    }
    return NULL;
}
static void ev_on_timer(ev_timer_event_t *timer, u32 timeout) {
    assert(timer && timer->active);
    timer->timeout = timer->period = timeout;
}
static void cancel(ev_timer_event_t **handle) {
    assert(*handle && (*handle)->active);
    (*handle)->active = false;
    *handle = NULL;
}
#define TL_ZB_TIMER_SCHEDULE schedule
#define TL_ZB_TIMER_CANCEL cancel
#include "scheduler_under_test.inc"
#if BASELINE
static void app_rejoinOnMotion(void) { }
#endif
static void advance(u32 elapsed) {
    uint64_t end = now_ms + elapsed;
    while (now_ms < end) {
        u32 step = (u32)(end - now_ms);
        for (unsigned i = 0; i < 8; i++)
            if (timers[i].active && timers[i].timeout < step) step = timers[i].timeout;
        now_ms += step;
        for (unsigned i = 0; i < 8; i++) if (timers[i].active) timers[i].timeout -= step;
        for (unsigned i = 0; i < 8; i++) if (timers[i].active && !timers[i].timeout) {
            s32 next = timers[i].cb(timers[i].data);
            if (next < 0) timers[i].active = false;
            else {
                if (next > 0) timers[i].period = (u32)next;
                timers[i].timeout = timers[i].period;
            }
        }
    }
}
static void start(void) {
    g_sensorAppCtx.linkDown = true;
    app_saveNwkChannel();
#if BASELINE
    zb_rejoinReq(app_rejoinChannelMask(), g_bdbAttrs.scanDuration);
    app_rejoinSchedule(rejoinBackoffTbl[0]);
#else
    app_rejoinSchedule(100);
#endif
}
int main(int argc, char **argv) {
    assert(argc == 2);
    const char *scenario = argv[1];
    if (!strcmp(scenario, "quiet") || !strcmp(scenario, "motion")) {
        start();
        for (unsigned minute = 0; minute < 480; minute++) {
            if (!strcmp(scenario, "motion")) app_rejoinOnMotion();
            advance(60000);
        }
        printf("{\"requests\":%u,\"full_scans\":%u,\"secure_full_scans\":%u,\"insecure_full_scans\":%u}\n",
               requests, full_scans, secure_full_scans, insecure_full_scans);
        return 0;
    }
#if !BASELINE
    if (!strcmp(scenario, "schedule")) {
        start();
        advance(100);
        for (unsigned i = 0; i < REJOIN_BACKOFF_STEPS; i++) {
            assert(requests == i + 1);
            assert(g_sensorAppCtx.timerRejoinBackoffEvt->timeout == rejoinBackoffTbl[i]);
            assert(last_mask == ((i & 3) >= 2 ? zb_apsChannelMaskGet() : 1UL << 11));
            advance(rejoinBackoffTbl[i]);
        }
    } else if (!strcmp(scenario, "motion_wrap")) {
        g_sensorAppCtx.linkDown = true;
        rj_chan = 11;
        rj_step = 10;
        motionRetryReady = true;
        app_rejoinSchedule(1800000);
        app_rejoinOnMotion();
        assert(rj_step == 10 && !motionRetryReady);
        advance(100);
        assert(requests == 1 && rj_step == 11 && full_scans == 1 && timerMotionCooldownEvt);
        advance(2000);
        app_rejoinOnMotion();
        assert(g_sensorAppCtx.timerRejoinBackoffEvt->timeout > 100);
        /* After 600 s (past two raw-clock wraps), motion must work. */
        advance(598000);
        assert(!timerMotionCooldownEvt && motionRetryReady);
        app_rejoinOnMotion();
        advance(100);
        assert(requests == 2 && rj_step == 12);
        for (unsigned i = 0; i < 20; i++) {
            advance(300000);
            app_rejoinOnMotion();
            advance(100);
        }
        assert(requests == 22 && full_scans >= 10);
        assert(secure_full_scans && insecure_full_scans);
    } else if (!strcmp(scenario, "busy")) {
        start();
        for (unsigned reason = 0; reason < 3; reason++) {
            forced_busy = reason == 0;
            bdb_busy = reason == 1;
            stack_busy = reason == 2;
            advance(reason ? 1000 : 100);
            app_rejoinOnMotion();
            assert(!requests && !rj_step && !timerMotionCooldownEvt);
        }
        forced_busy = bdb_busy = stack_busy = false;
        request_status = 1;
        bool mode = rj_secMode;
        advance(1000);
        assert(!requests && !rj_step && rj_secMode == mode);
        request_status = RET_OK;
        advance(1000);
        assert(requests == 1 && rj_step == 1 && rj_secMode != mode);
        app_rejoinOnMotion(); /* SDK rejoin is still active. */
        assert(timerMotionCooldownEvt && !motionRetryReady);
    } else if (!strcmp(scenario, "success")) {
        start();
        advance(100);
        advance(1000);
        app_rejoinOnMotion();
        assert(timerMotionCooldownEvt);
        cancel(&g_sensorAppCtx.timerRejoinBackoffEvt);
        g_sensorAppCtx.linkDown = false;
        rj_step = 0;
        app_motionCooldownClear();
        advance(1000000);
        app_rejoinOnMotion();
        assert(requests == 1 && !timerMotionCooldownEvt);
        g_sensorAppCtx.linkDown = true;
        factory_new = true;
        app_rejoinSchedule(100);
        advance(100);
        app_rejoinOnMotion();
        assert(requests == 1 && !g_sensorAppCtx.timerRejoinBackoffEvt && !timerMotionCooldownEvt);
    } else if (!strcmp(scenario, "pool")) {
        g_sensorAppCtx.linkDown = true;
        no_timers = true;
        app_rejoinOnMotion();
        assert(!timerMotionCooldownEvt && !g_sensorAppCtx.timerRejoinBackoffEvt && !requests);
        no_timers = false;
        start();
        no_timers = true; /* Automatic timer survives cooldown allocation failure. */
        advance(100);
        assert(requests == 1 && !motionRetryReady && !timerMotionCooldownEvt);
        no_timers = false;
        advance(2000);
        assert(requests == 2 && timerMotionCooldownEvt && !motionRetryReady);
    } else if (!strcmp(scenario, "shared_floor")) {
        start();
        advance(160100);
        assert(requests == 8);
        advance(299900);
        app_rejoinOnMotion();
        assert(requests == 8 && !motionRetryReady);
        advance(100); /* Automatic retry owns the same five-minute motion floor. */
        assert(requests == 9 && !motionRetryReady);
        advance(300000);
        assert(motionRetryReady);
        app_rejoinOnMotion();
        advance(100);
        assert(requests == 10 && !motionRetryReady);
        assert(g_sensorAppCtx.timerRejoinBackoffEvt->timeout == 1800000);
    } else if (!strcmp(scenario, "unknown_channel")) {
        g_sensorAppCtx.linkDown = true;
        app_rejoinSchedule(100);
        advance(2100);
        assert(requests == 2 && full_scans == 2);
        assert(secure_full_scans == 1 && insecure_full_scans == 1);
    } else if (!strcmp(scenario, "counter_wrap")) {
        g_sensorAppCtx.linkDown = true;
        rj_chan = 11;
        rj_step = 254;
        app_rejoinSchedule(100);
        advance(100);
        advance(1800000);
        assert(requests == 2 && full_scans == 2 && rj_step == 252);
        advance(1800000);
        assert(requests == 3 && full_scans == 2 && rj_step == 253);
        assert(secure_full_scans == 1 && insecure_full_scans == 1);
    } else if (!strcmp(scenario, "button")) {
        /* Button recovery remains usable before the lost-parent callback. */
        rj_step = 7;
        app_rejoinNow();
        assert(g_sensorAppCtx.linkDown && !rj_step);
        advance(100);
        assert(requests == 1);
        app_rejoinNow();
        assert(rj_step == 1); /* Cannot reset an active SDK attempt. */
    } else assert(!"unknown scenario");
#endif
    puts("ok");
    return 0;
}
