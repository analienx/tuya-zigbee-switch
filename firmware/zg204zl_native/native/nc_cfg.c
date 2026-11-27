#include "nc.h"
nc_state_t nc;

uint8_t nc_cfg_parse(const uint8_t *p, unsigned n, nc_cfg_t *out) {
    nc_cfg_t c;
    if (n != 12) return NC_MALFORMED;
    if (p[0] != 1 || p[3] || (p[1] & ~(NC_TIME | NC_LUX))) return NC_INVALID_FIELD;
    c.version = 1; c.flags = p[1]; c.level = p[2]; c.reserved = 0;
    c.start = nc_le16(p + 4); c.end = nc_le16(p + 6);
    c.fade_in = nc_le16(p + 8); c.fade_out = nc_le16(p + 10);
    if (!c.level || c.level > 254 || c.start > 1439 || c.end > 1439 ||
        ((c.flags & NC_TIME) && c.start == c.end) ||
        c.fade_in > 1200 || c.fade_out > 1200) return NC_INVALID_VALUE;
    *out = c;
    return NC_OK;
}

void nc_cfg_export(void) {
    uint8_t *p = nc.cfg_attr;
    p[0] = 12; p[1] = 1; p[2] = nc.cfg.flags; p[3] = nc.cfg.level; p[4] = 0;
    nc_put16(p + 5, nc.cfg.start); nc_put16(p + 7, nc.cfg.end);
    nc_put16(p + 9, nc.cfg.fade_in); nc_put16(p + 11, nc.cfg.fade_out);
}

void nc_init(void) {
    uint8_t raw[12];
    memset(&nc, 0, sizeof(nc));
    nc.cfg.version = 1; nc.cfg.level = 254;
    nc.cfg.flags = nc_port_lux_target() == 0xffff ? 0 : NC_LUX;
    nc.cfg.fade_in = 10; nc.cfg.fade_out = 30;
    if (nc_port_nv_read(raw) && nc_cfg_parse(raw, 12, &nc.cfg) == NC_OK)
        nc.cfg_present = 1;
    nc_cfg_export();
    nc.last_aps = nc.last_zcl = 0xff;
    nc.clock.refresh = nc_port_now();
}

uint8_t nc_configure(const uint8_t *p, unsigned n) {
    nc_cfg_t candidate;
    uint8_t readback[12], status = nc_cfg_parse(p, n, &candidate);
    if (status) return status;
    if ((candidate.flags & NC_LUX) && nc_port_lux_target() == 0xffff) return NC_INVALID_VALUE;
    if (nc.cfg_present && !memcmp(p, nc.cfg_attr + 1, 12)) return NC_OK;
    if (nc_port_battery() < 2200) return NC_FAILURE;
    if (!nc_port_nv_write(p) || !nc_port_nv_read(readback) || memcmp(p, readback, 12))
        return NC_HARDWARE_FAILURE;
    nc.cfg = candidate; nc.cfg_present = 1;
    nc_cfg_export();
    if (!(candidate.flags & NC_TIME)) nc.clock.pending = nc.clock.valid = 0;
    else nc.clock.refresh = nc_port_now();
    return NC_OK;
}

void nc_diagnostics(void) {
    uint8_t *p = nc.diag_attr;
    unsigned i, onoff = 0, level = 0;
    uint32_t age = (nc_port_now() - nc.clock.at) / 60000u;
    for (i = 0; i < NC_MAX_DST; i++) if (!nc.dst[i].addr.group) {
        if (nc.cfg_present && (nc.dst[i].caps & NC_LEVEL)) level++;
        else if (nc.dst[i].caps & NC_ONOFF) onoff++;
    }
    p[0] = 12; p[1] = 1; p[2] = nc.phase | nc.reason << 2;
    p[3] = nc_local_minute() >= 0;
    nc_put16(p + 4, age > 65535 ? 65535 : (uint16_t)age);
    p[6] = nc.last_aps; p[7] = nc.last_zcl;
    p[8] = nc.cfg_present | (nc_tx_off_unknown() ? 2 : 0) | (nc.stale_on ? 4 : 0);
    p[9] = onoff; p[10] = level; p[11] = nc.group_count; p[12] = nc.last_zcl_command;
}
