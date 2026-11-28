#include "nc.h"

static int same(const nc_addr_t *a, const nc_addr_t *b) {
    return !memcmp(a, b, sizeof(*a));
}

static int eligible(const nc_dst_t *r) {
    return (r->caps & NC_ONOFF) || (nc.want_native && (r->caps & NC_LEVEL));
}

static int flying(const nc_dst_t *r) {
    return r->state == TX_FLY || r->state == TX_BLOCKED;
}

void nc_tx_scan(void) {
    unsigned pass, i, j;
    nc_binding_t b;
    nc.unicast_count = nc.group_count = 0;
    for (i = 0; i < NC_MAX_DST; i++) nc.dst[i].caps &= NC_OCCUPIED;
    /* Match retained rows first. Free removed rows before allocating replacements,
     * so a full table can be replaced in one scan without losing new bindings. */
    for (pass = 0; pass < 2; pass++) {
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = 0;
        if (!nc_port_binding(i, &b)) continue;
        for (j = 0; j < NC_MAX_DST; j++)
            if ((nc.dst[j].caps & NC_OCCUPIED) && same(&nc.dst[j].addr, &b.addr)) {
                r = &nc.dst[j]; break;
            }
        if (!r && pass) for (j = 0; j < NC_MAX_DST; j++) if (!nc.dst[j].caps) {
            r = &nc.dst[j]; memset(r, 0, sizeof(*r)); r->addr = b.addr;
            r->caps = NC_OCCUPIED; r->state = TX_READY;
            if (nc.wanted) r->flags = NC_PENDING;
            break;
        }
        if (r) r->caps |= b.caps;
    }
    if (!pass) for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        if (!(r->caps & 3) && !flying(r)) memset(r, 0, sizeof(*r));
    }
    }
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        if (!(r->caps & 3)) {
            if (!flying(r)) memset(r, 0, sizeof(*r));
            else r->flags &= NC_FLY_LEVEL | NC_FLY_REPEAT;
        } else if (r->addr.group) nc.group_count++;
        else nc.unicast_count++;
    }
}

void nc_tx_want(int on, uint8_t level, uint16_t fade, int native) {
    unsigned i;
    nc_tx_scan();
    nc.wanted = 1; nc.on = !!on; nc.want_level = level; nc.want_native = !!native;
    nc.born = nc_port_now(); nc.fade_end = nc.born + (uint32_t)fade * 100u;
    nc.generation++;
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        if (!(r->caps & 3)) continue;
        r->flags = (r->flags & (NC_OFF_UNKNOWN | NC_FLY_LEVEL | NC_FLY_REPEAT | NC_REJECTED)) | NC_PENDING;
        if (!on && !r->addr.group) r->flags |= NC_OFF_UNKNOWN;
        if (native && r->addr.group && (r->caps & NC_LEVEL)) r->flags |= NC_REPEAT;
        if (!flying(r)) { r->state = TX_READY; r->tries = 0; r->due = nc.born; }
    }
}

unsigned nc_tx_inflight(void) {
    unsigned i, n = 0;
    for (i = 0; i < NC_MAX_DST; i++) n += flying(&nc.dst[i]);
    return n;
}

int nc_tx_blocked(void) {
    unsigned i;
    for (i = 0; i < NC_MAX_DST; i++) if (nc.dst[i].state == TX_BLOCKED) return 1;
    return nc.clock.sending == 2;
}

int nc_tx_off_unknown(void) {
    unsigned i;
    for (i = 0; i < NC_MAX_DST; i++) if (nc.dst[i].flags & NC_OFF_UNKNOWN) return 1;
    return 0;
}

static void unsuccessful(nc_dst_t *r, uint8_t status) {
    nc.last_aps = status;
    if (!(r->caps & 3)) { memset(r, 0, sizeof(*r)); return; }
    if (r->fly_gen != nc.generation) {
        r->state = TX_READY; r->tries = 0; r->due = nc_port_now(); return;
    }
    if (++r->tries > 2) {
        r->state = TX_FAILED;
        if (!nc.on && (!r->addr.group || !(r->flags & NC_HOP_OK))) r->flags |= NC_OFF_UNKNOWN;
    } else {
        r->state = TX_WAIT; r->due = nc_port_now() + (r->tries == 1 ? 2000u : 10000u);
    }
}

void nc_confirm(const nc_addr_t *addr, uint8_t aps, uint16_t cluster, uint8_t status) {
    unsigned i;
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        if (!flying(r) || r->aps != aps || !same(&r->addr, addr) ||
            cluster != ((r->flags & NC_FLY_LEVEL) ? 8 : 6)) continue;
        if (status) { unsuccessful(r, status); return; }
        nc.last_aps = 0;
        if (!(r->caps & 3)) { memset(r, 0, sizeof(*r)); return; }
        if (r->fly_gen != nc.generation) {
            r->state = TX_READY; r->due = nc_port_now(); r->tries = 0; return;
        }
        if (r->flags & NC_REJECTED) {
            r->state = TX_FAILED; r->flags &= ~NC_PENDING; return;
        }
        r->flags &= ~NC_OFF_UNKNOWN;
        r->flags |= NC_HOP_OK;
        if (r->addr.group && (r->flags & NC_REPEAT)) {
            r->flags &= ~NC_REPEAT;
            r->state = TX_WAIT; r->due = nc_port_now() + 2000u;
        } else { r->state = TX_DONE; r->flags &= ~NC_PENDING; }
        return;
    }
}

void nc_rejection(const nc_addr_t *addr, uint8_t seq, uint16_t cluster,
                  uint8_t cmd, uint8_t status) {
    unsigned i;
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        if (addr->group || !r->caps || !same(addr, &r->addr) || r->zcl != seq ||
            r->fly_gen != nc.generation || cluster != ((r->flags & NC_FLY_LEVEL) ? 8 : 6) ||
            cmd != ((r->flags & NC_FLY_LEVEL) ? 4 : (r->fly_on ? 1 : 0))) continue;
        nc.last_zcl = status;
        nc.last_zcl_command = (cluster == 8 ? 0x80 : 0) | cmd;
        if (status) {
            r->flags |= NC_REJECTED;
            if (!flying(r)) { r->state = TX_FAILED; r->flags &= ~NC_PENDING; }
            if (!r->fly_on) r->flags |= NC_OFF_UNKNOWN;
        }
        return;
    }
}

void nc_tx_kick(void) {
    unsigned i;
    if (!nc.on || nc_port_now() - nc.born < 60000u) return;
    for (i = 0; i < NC_MAX_DST; i++) if (nc.dst[i].state == TX_FAILED && !nc.dst[i].addr.group) {
        nc.born = nc_port_now(); nc.fade_end = nc.born + (uint32_t)nc.fade_in * 100u;
        for (i = 0; i < NC_MAX_DST; i++) if (nc.dst[i].state == TX_FAILED && !nc.dst[i].addr.group) {
            nc.dst[i].state = TX_READY; nc.dst[i].tries = 0; nc.dst[i].flags |= NC_PENDING;
            nc.dst[i].due = nc.born;
        }
        break;
    }
}

void nc_tx_service(void) {
    unsigned i, in_flight = nc_tx_inflight();
    uint32_t now = nc_port_now();
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        uint8_t frame[6], n = 3, status, aps = 0;
        int level;
        int32_t remaining;
        if (flying(r)) {
            /* A wall-clock deadline cannot cancel retries inside the binary
             * stack. Quarantine the slot; only its real completion retires it. */
            if (r->state == TX_FLY && nc_due(now, r->due)) {
                r->state = TX_BLOCKED; nc.last_aps = 0xf1;
                if (!r->fly_on && (!r->addr.group || !(r->flags & NC_HOP_OK)))
                    r->flags |= NC_OFF_UNKNOWN;
            }
            continue;
        }
        if (!(r->flags & NC_PENDING) || !eligible(r) || r->state == TX_FAILED) continue;
        if (now - nc.born >= (nc.on ? 60000u : 1800000u)) {
            r->state = TX_FAILED;
            if (nc.on) nc.stale_on = 1;
            continue;
        }
        if (in_flight >= 2 || nc.clock.pending || nc.clock.sending || !nc_port_joined() ||
            (r->state == TX_WAIT && !nc_due(now, r->due))) continue;
        level = nc.want_native && (r->caps & NC_LEVEL);
        frame[0] = level ? 0x11 : 0x01; frame[1] = nc_port_sequence();
        frame[2] = nc.on ? 1 : 0;
        if (level) {
            uint16_t t;
            remaining = (int32_t)(nc.fade_end - now);
            t = remaining > 0 ? (uint16_t)((remaining + 99) / 100) : 0;
            frame[2] = 4; frame[3] = nc.on ? nc.want_level : 0;
            nc_put16(frame + 4, t); n = 6;
        }
        r->fly_gen = nc.generation; r->fly_on = nc.on; r->zcl = frame[1];
        r->flags = (r->flags & ~(NC_FLY_LEVEL | NC_REJECTED)) | (level ? NC_FLY_LEVEL : 0);
        status = nc_port_send(&r->addr, level ? 8 : 6, frame, n, !r->addr.group, &aps);
        if (status) unsuccessful(r, status);
        else { r->aps = aps; r->state = TX_FLY; r->due = now + 30000u; in_flight++; }
    }
}

uint32_t nc_tx_delay(void) {
    unsigned i;
    uint32_t delay = 0, now = nc_port_now();
    int available = nc_tx_inflight() < 2 && !nc.clock.pending && !nc.clock.sending && nc_port_joined();
    for (i = 0; i < NC_MAX_DST; i++) {
        nc_dst_t *r = &nc.dst[i];
        uint32_t d;
        if (r->state == TX_READY && (r->flags & NC_PENDING) && eligible(r) && available) return 1;
        if (r->state != TX_FLY && !(r->state == TX_WAIT && (r->flags & NC_PENDING))) continue;
        d = nc_due(now, r->due) ? 1 : r->due - now;
        if (!delay || d < delay) delay = d;
    }
    return delay;
}
