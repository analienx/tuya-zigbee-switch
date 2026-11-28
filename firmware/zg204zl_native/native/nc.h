#ifndef NC_H
#define NC_H
#include <stdint.h>
#include <string.h>

#define NC_CLUSTER 0xfc70
#define NC_MANUFACTURER 0x1141
#define NC_MAX_DST 12
#define NC_TIME 1
#define NC_LUX 2
#define NC_ONOFF 1
#define NC_LEVEL 2
#define NC_OCCUPIED 0x80
#define NC_PENDING 1
#define NC_OFF_UNKNOWN 2
#define NC_REPEAT 4
#define NC_HOP_OK 8
#define NC_FLY_LEVEL 16
#define NC_FLY_REPEAT 32
#define NC_REJECTED 64
#define NC_OK 0
#define NC_FAILURE 1
#define NC_NOT_AUTHORIZED 0x7e
#define NC_MALFORMED 0x80
#define NC_INVALID_FIELD 0x85
#define NC_INVALID_VALUE 0x87
#define NC_HARDWARE_FAILURE 0xc0

enum { NC_IDLE, NC_ACTIVE, NC_RELEASING };
enum { NC_ADMIT, NC_MASTER_OFF, NC_TIME_UNKNOWN, NC_TIME_OUTSIDE,
       NC_LUX_NO_THRESHOLD, NC_LUX_BRIGHT, NC_RESUMED, NC_NO_BINDING };
enum { TX_DONE, TX_READY, TX_FLY, TX_WAIT, TX_FAILED, TX_BLOCKED };
typedef struct {
    uint8_t version, flags, level, reserved;
    uint16_t start, end, fade_in, fade_out;
} nc_cfg_t;
typedef struct { uint8_t id[8], ep, group; } nc_addr_t;
typedef struct { nc_addr_t addr; uint8_t caps; } nc_binding_t;
typedef struct {
    uint32_t due;
    uint16_t fly_gen;
    uint8_t caps, state;
    nc_addr_t addr;
    uint8_t flags, aps, zcl, fly_on, tries, padding;
} __attribute__((aligned(4))) nc_dst_t;
typedef struct {
    uint32_t utc, at, valid_until, refresh, ds, de, deadline, tx_deadline;
    int32_t tz, shift;
    uint8_t valid, pending, seq, failures, sending, aps;
} __attribute__((aligned(4))) nc_clock_t;
typedef struct {
    nc_cfg_t cfg;
    nc_dst_t dst[NC_MAX_DST];
    nc_clock_t clock;
    uint32_t edge, grace, born, fade_end;
    uint16_t generation, fade_in, fade_out;
    uint8_t cfg_present, phase, reason, reoff, pending_motion;
    uint8_t level, cycle_native, on, wanted, want_level, want_native;
    uint8_t last_aps, last_zcl, last_zcl_command, stale_on, unicast_count, group_count;
    uint8_t cfg_attr[13], diag_attr[13];
} __attribute__((aligned(4))) nc_state_t;
extern nc_state_t nc;
typedef char nc_config_is_12_bytes[(sizeof(nc_cfg_t) == 12) ? 1 : -1];
typedef char nc_timer_alignment[(__builtin_offsetof(nc_dst_t, due) % 4 == 0 && sizeof(nc_dst_t) % 4 == 0) ? 1 : -1];
typedef char nc_runtime_budget[(sizeof(nc_state_t) + 16 <= 512) ? 1 : -1];

static inline uint16_t nc_le16(const uint8_t *p) {
    return (uint16_t)p[0] | (uint16_t)p[1] << 8;
}
static inline void nc_put16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static inline int nc_due(uint32_t now, uint32_t due) { return (int32_t)(now - due) >= 0; }

/* Port functions are the only dependencies on the Telink SDK. */
uint32_t nc_port_now(void);
int nc_port_joined(void);
int nc_port_master(void);
int nc_port_occupied(void);
uint16_t nc_port_lux_target(void);
int nc_port_dark(void); /* Fresh ADC read; called before sending anything. */
uint16_t nc_port_battery(void);
int nc_port_nv_read(uint8_t raw[12]);
int nc_port_nv_write(const uint8_t raw[12]);
int nc_port_binding(unsigned index, nc_binding_t *binding);
uint8_t nc_port_sequence(void);
uint8_t nc_port_send(const nc_addr_t *addr, uint16_t cluster,
                     const uint8_t *frame, unsigned length, int ack, uint8_t *aps);
void nc_port_fast_poll(void);
void nc_port_arm(uint32_t delay);

void nc_init(void);
uint8_t nc_cfg_parse(const uint8_t *raw, unsigned length, nc_cfg_t *out);
uint8_t nc_configure(const uint8_t *raw, unsigned length);
void nc_cfg_export(void);
void nc_diagnostics(void);
void nc_motion(void);
void nc_absence(void);
void nc_master(int enabled);
void nc_clock_ready(void);
void nc_service(void);
void nc_tx_scan(void);
void nc_tx_want(int on, uint8_t level, uint16_t fade, int native);
void nc_tx_kick(void);
int nc_tx_off_unknown(void);
unsigned nc_tx_inflight(void);
int nc_tx_blocked(void);
void nc_tx_service(void);
uint32_t nc_tx_delay(void);
void nc_confirm(const nc_addr_t *addr, uint8_t aps, uint16_t cluster,
                uint8_t status);
void nc_rejection(const nc_addr_t *addr, uint8_t seq, uint16_t cluster,
                  uint8_t cmd, uint8_t status);
void nc_time_service(void);
void nc_time_confirm(uint8_t aps, uint8_t status);
int nc_local_minute(void);
void nc_time_response(uint8_t seq, const uint8_t *payload, unsigned length);
int nc_receive(uint16_t source, uint8_t endpoint, uint16_t profile, int unicast,
               int secured, const nc_addr_t *address, uint16_t cluster,
               const uint8_t *frame, unsigned length);
#endif
