#include "nc.h"

typedef struct {
    nc_addr_t addr;
    uint16_t cluster;
    uint8_t frame[32], length, ack, aps;
} host_frame_t;
static host_frame_t frames[65536];
static nc_binding_t bindings[12];
static uint8_t nv[12], nv_present, nv_failure, read_failure;
static uint32_t now, arm_delay;
static unsigned count, nv_writes, lux_reads, fast_polls;
static int master, occupied, dark, joined, send_status;
static uint16_t lux_target, battery;
static uint8_t seq, counter;

void host_reset(void) {
    memset(bindings, 0, sizeof(bindings)); memset(nv, 0, sizeof(nv));
    now = count = nv_writes = lux_reads = fast_polls = arm_delay = 0;
    nv_present = nv_failure = read_failure = seq = counter = 0;
    master = occupied = dark = joined = 1; send_status = 0;
    lux_target = 13000; battery = 3000; nc_init();
}
void host_advance(uint32_t ms) { now += ms; }
void host_time(uint32_t ms) { now = ms; }
void host_master(int value) { master = value; nc_master(value); }
void host_occupied(int value) { occupied = value; }
void host_dark(int value) { dark = value; }
void host_joined(int value) { joined = value; }
void host_lux_target(uint16_t value) { lux_target = value; }
void host_battery(uint16_t value) { battery = value; }
void host_nv_fail(int value) { nv_failure = value; }
void host_read_fail(int value) { read_failure = value; }
void host_send_status(int value) { send_status = value; }
void host_counter(uint8_t value) { counter = value; }
void host_bind(unsigned index, unsigned id, unsigned ep, unsigned caps, int group) {
    nc_binding_t *b = &bindings[index]; memset(b, 0, sizeof(*b));
    b->caps = caps; b->addr.group = !!group; b->addr.ep = group ? 0 : ep;
    nc_put16(b->addr.id, id);
}
unsigned host_count(void) { return count; }
unsigned host_writes(void) { return nv_writes; }
unsigned host_lux_reads(void) { return lux_reads; }
unsigned host_fast_polls(void) { return fast_polls; }
unsigned host_inflight(void) { return nc_tx_inflight() + !!(nc.clock.pending || nc.clock.sending); }
unsigned host_generation(void) { return nc.generation; }
unsigned host_phase(void) { return nc.phase; }
unsigned host_reason(void) { return nc.reason; }
unsigned host_size(void) { return sizeof(nc); }
unsigned host_delay(void) { return arm_delay; }
unsigned host_cluster(unsigned i) { return frames[i].cluster; }
unsigned host_ack_option(unsigned i) { return frames[i].ack; }
unsigned host_frame(unsigned i, uint8_t *out) { memcpy(out, frames[i].frame, frames[i].length); return frames[i].length; }
void host_ack(unsigned i, uint8_t status) {
    if (frames[i].cluster == 10) nc_time_confirm(frames[i].aps, status);
    else nc_confirm(&frames[i].addr, frames[i].aps, frames[i].cluster, status);
}
void host_wrong_ack(unsigned i) { nc_confirm(&frames[i].addr, frames[i].aps + 1, frames[i].cluster, 0); }
void host_other_endpoint_ack(unsigned i) {
    nc_addr_t address = frames[i].addr; address.ep++;
    nc_confirm(&address, frames[i].aps, frames[i].cluster, 0);
}
void host_other_address_ack(unsigned i) {
    nc_addr_t address = frames[i].addr; address.id[0] ^= 1;
    nc_confirm(&address, frames[i].aps, frames[i].cluster, 0);
}
void host_reject(unsigned i, uint8_t status) { nc_rejection(&frames[i].addr, frames[i].frame[1], frames[i].cluster, frames[i].frame[2], status); }
void host_diag(uint8_t *out) { nc_diagnostics(); memcpy(out, nc.diag_attr + 1, 12); }
void host_config(uint8_t *out) { memcpy(out, nc.cfg_attr + 1, 12); }
uint32_t nc_port_now(void) { return now; }
int nc_port_joined(void) { return joined; }
int nc_port_master(void) { return master; }
int nc_port_occupied(void) { return occupied; }
uint16_t nc_port_lux_target(void) { return lux_target; }
int nc_port_dark(void) { lux_reads++; return dark; }
uint16_t nc_port_battery(void) { return battery; }
int nc_port_nv_read(uint8_t raw[12]) { if (!nv_present || read_failure) return 0; memcpy(raw, nv, 12); return 1; }
int nc_port_nv_write(const uint8_t raw[12]) { nv_writes++; if (nv_failure) return 0; memcpy(nv, raw, 12); nv_present = 1; return 1; }
int nc_port_binding(unsigned i, nc_binding_t *b) { *b = bindings[i]; return b->caps != 0; }
uint8_t nc_port_sequence(void) { return seq++; }
uint8_t nc_port_send(const nc_addr_t *a, uint16_t cluster, const uint8_t *f,
                     unsigned n, int ack, uint8_t *aps) {
    host_frame_t *out;
    if (send_status) return send_status;
    if (count == 65536 || n > 32) return 0xff;
    out = &frames[count++]; out->addr = *a; out->cluster = cluster;
    memcpy(out->frame, f, n); out->length = n; out->ack = ack;
    out->aps = *aps = counter++; return 0;
}
void nc_port_fast_poll(void) { fast_polls++; }
void nc_port_arm(uint32_t delay) { arm_delay = delay; }
