#include "nc.h"

static uint8_t admit(void) {
    int m;
    if (!nc_port_master()) return NC_MASTER_OFF;
    if (nc.cfg.flags & NC_TIME) {
        m = nc_local_minute();
        if (m < 0) return NC_TIME_UNKNOWN;
        if (!(nc.cfg.start < nc.cfg.end ? (m >= nc.cfg.start && m < nc.cfg.end) :
              (m >= nc.cfg.start || m < nc.cfg.end))) return NC_TIME_OUTSIDE;
    }
    if (nc.cfg.flags & NC_LUX) {
        if (nc_port_lux_target() == 0xffff) return NC_LUX_NO_THRESHOLD;
        if (!nc_port_dark()) return NC_LUX_BRIGHT;
    }
    return NC_ADMIT;
}

void nc_motion(void) {
    uint32_t now = nc_port_now();
    if (!nc_port_master()) {
        if (nc.phase != NC_IDLE) nc_master(0);
        if (nc_tx_off_unknown()) nc.reoff = 1;
        nc.pending_motion = 0; nc.reason = NC_MASTER_OFF; return;
    }
    if (nc.phase == NC_ACTIVE) { nc_tx_kick(); return; }
    if (nc.phase == NC_RELEASING && !nc_due(now, nc.grace)) {
        nc.phase = NC_ACTIVE; nc.reason = NC_RESUMED;
        nc_tx_want(1, nc.level, nc.fade_in, nc.cycle_native); return;
    }
    nc.phase = NC_IDLE;
    nc.reason = admit(); /* This may cold-sleep on low battery; no TX precedes it. */
    if (nc.reason == NC_ADMIT) {
        nc.level = nc.cfg.level; nc.fade_in = nc.cfg.fade_in; nc.fade_out = nc.cfg.fade_out;
        nc.cycle_native = nc.cfg_present;
        nc.phase = NC_ACTIVE; nc.reoff = nc.pending_motion = 0;
        nc_tx_want(1, nc.level, nc.fade_in, nc.cycle_native);
    } else {
        if (nc_tx_off_unknown()) nc.reoff = 1;
        nc.pending_motion = nc.reason == NC_TIME_UNKNOWN; nc.edge = now;
    }
}

void nc_absence(void) {
    if (nc.phase == NC_ACTIVE) {
        nc.phase = NC_RELEASING;
        nc.grace = nc_port_now() + (uint32_t)nc.fade_out * 100u + 2000u;
        nc_tx_want(0, 0, nc.fade_out, nc.cycle_native);
    } else if (nc.reoff) {
        nc.reoff = 0; nc_tx_want(0, 0, nc.fade_out, nc.cycle_native);
    }
    nc.pending_motion = 0;
}

void nc_master(int enabled) {
    if (!enabled) {
        nc.pending_motion = 0;
        if (nc.phase != NC_IDLE || nc_tx_off_unknown())
            nc_tx_want(0, 0, nc.fade_out, nc.cycle_native);
        nc.phase = NC_IDLE;
    }
}

void nc_clock_ready(void) {
    int fresh = nc.pending_motion && nc_port_occupied() &&
                nc_port_now() - nc.edge <= 10000u;
    nc.pending_motion = 0;
    if (fresh && nc.phase == NC_IDLE) nc_motion();
}

void nc_service(void) {
    uint32_t delay;
    nc_tx_scan();
    nc_tx_service();
    nc_time_service();
    delay = nc_tx_delay();
    if (nc.clock.pending) {
        uint32_t d = nc_due(nc_port_now(), nc.clock.deadline) ? 1 : nc.clock.deadline - nc_port_now();
        if (!delay || d < delay) delay = d;
    }
    if (nc.clock.sending == 1) {
        uint32_t d = nc_due(nc_port_now(), nc.clock.tx_deadline) ? 1 : nc.clock.tx_deadline - nc_port_now();
        if (!delay || d < delay) delay = d;
    }
    nc_port_arm(delay);
}
