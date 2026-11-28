#include "nc.h"
static const uint16_t ids[7] = {0, 1, 2, 3, 4, 5, 9};
static const uint8_t types[7] = {0xe2, 0x18, 0x2b, 0x23, 0x23, 0x2b, 0xe2};

static void failed(void) {
    static const uint32_t delays[] = {30000, 120000, 600000, 1800000, 3600000};
    unsigned i = nc.clock.failures;
    if (i < 4) nc.clock.failures++;
    else i = 4;
    nc.clock.pending = 0; nc.clock.refresh = nc_port_now() + delays[i];
}

void nc_time_service(void) {
    uint8_t frame[17], aps;
    nc_addr_t coordinator;
    unsigned i;
    if (nc.clock.sending == 1 && nc_due(nc_port_now(), nc.clock.tx_deadline))
        nc.clock.sending = 2;
    if (nc.clock.valid && nc_due(nc_port_now(), nc.clock.valid_until)) nc.clock.valid = 0;
    if (!(nc.cfg.flags & NC_TIME)) { nc.clock.pending = 0; return; }
    if (nc.clock.pending) {
        if (nc_due(nc_port_now(), nc.clock.deadline)) failed();
        return;
    }
    /* Serialize Time against lighting, including the coordinator's report binding.
     * A response timeout does not retire an APS request inside the stack. */
    if (nc.clock.sending || !nc_due(nc_port_now(), nc.clock.refresh) ||
        !nc_port_joined() || nc_tx_inflight())
        return;
    memset(&coordinator, 0, sizeof(coordinator)); coordinator.ep = 1;
    frame[0] = 0x10; frame[1] = nc_port_sequence(); frame[2] = 0;
    for (i = 0; i < 7; i++) nc_put16(frame + 3 + 2 * i, ids[i]);
    if (nc_port_send(&coordinator, 0x000a, frame, 17, 1, &aps)) { failed(); return; }
    nc.clock.seq = frame[1]; nc.clock.pending = nc.clock.sending = 1;
    nc.clock.aps = aps; nc.clock.tx_deadline = nc_port_now() + 30000;
    nc.clock.deadline = nc_port_now() + 3000;
    nc_port_fast_poll();
}

void nc_time_confirm(uint8_t aps, uint8_t status) {
    if (!nc.clock.sending || aps != nc.clock.aps) return;
    nc.clock.sending = 0;
    if (status && nc.clock.pending) failed();
}

void nc_time_response(uint8_t seq, const uint8_t *p, unsigned n) {
    uint32_t v[7] = {0}, rel, refresh, validity;
    uint8_t seen = 0, ok = 0;
    unsigned i, len;
    if (!nc.clock.pending || !(nc.cfg.flags & NC_TIME) || seq != nc.clock.seq ||
        nc_due(nc_port_now(), nc.clock.deadline)) return;
    while (n) {
        uint16_t id;
        uint8_t status;
        if (n < 3) return;
        id = nc_le16(p); status = p[2]; p += 3; n -= 3;
        for (i = 0; i < 7 && ids[i] != id; i++) {}
        if (i == 7 || (seen & (1u << i))) return;
        seen |= 1u << i;
        if (status) continue;
        len = i == 1 ? 1 : 4;
        if (n < len + 1 || p[0] != types[i]) return;
        v[i] = p[1];
        if (len == 4) v[i] |= (uint32_t)p[2] << 8 | (uint32_t)p[3] << 16 | (uint32_t)p[4] << 24;
        ok |= 1u << i; p += len + 1; n -= len + 1;
    }
    if (seen != 0x7f || (ok & 7) != 7 || !(v[1] & 3) || v[0] < 820540800u ||
        v[0] == UINT32_MAX || (int32_t)v[2] < -50400 || (int32_t)v[2] > 50400) return;
    if ((ok & 0x38) != 0 && (ok & 0x38) != 0x38) return;
    if ((ok & 0x38) == 0x38) {
        if (v[3] == UINT32_MAX && v[4] == UINT32_MAX && !v[5]) {
            v[3] = v[4] = 0;
        } else if (v[3] == UINT32_MAX || v[4] == UINT32_MAX || v[4] <= v[3] ||
                   (int32_t)v[5] < -7200 || (int32_t)v[5] > 7200) return;
    }
    rel = 43200;
    if (ok & 0x40) {
        if (v[6] == UINT32_MAX || v[6] <= v[0]) return;
        rel = v[6] - v[0];
    }
    /* Clamp before adding grace, so untrusted large values cannot wrap. */
    refresh = rel < 21570 ? rel + 30 : 21600;
    validity = rel < 42900 ? rel + 300 : 43200;
    nc.clock.utc = v[0]; nc.clock.at = nc_port_now(); nc.clock.tz = (int32_t)v[2];
    nc.clock.ds = v[3]; nc.clock.de = v[4]; nc.clock.shift = (int32_t)v[5];
    nc.clock.refresh = nc.clock.at + refresh * 1000u;
    nc.clock.valid_until = nc.clock.at + validity * 1000u;
    nc.clock.valid = 1; nc.clock.pending = nc.clock.failures = 0;
    nc_clock_ready();
}

int nc_local_minute(void) {
    uint32_t utc, local;
    int32_t offset;
    if (!nc.clock.valid) return -1;
    if (nc_due(nc_port_now(), nc.clock.valid_until)) { nc.clock.valid = 0; return -1; }
    utc = nc.clock.utc + (nc_port_now() - nc.clock.at) / 1000u;
    if (utc < nc.clock.utc) return -1;
    offset = nc.clock.tz;
    if (nc.clock.de && utc >= nc.clock.ds && utc <= nc.clock.de) offset += nc.clock.shift;
    local = utc + (uint32_t)offset;
    if ((offset >= 0 && local < utc) || (offset < 0 && local > utc)) return -1;
    return (int)((local / 60u) % 1440u);
}
