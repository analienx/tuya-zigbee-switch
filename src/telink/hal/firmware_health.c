#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#pragma pack(pop)
#include "hal/firmware_health.h"
#include "hal/timer.h"
#include "hal/zigbee.h"

firmware_health_t firmware_health;
/* ZCL octet string: one length byte followed by schema-1 diagnostic bytes. */
uint8_t firmware_health_snapshot[49] = {48, 1};
extern volatile u16 T_evtExcept[4];

static void put32(uint8_t *p, uint32_t value) {
    for (uint8_t i = 0; i < 4; i++)
        p[i] = (uint8_t)(value >> (8 * i));
}

void hal_firmware_health_update(void) {
    static uint32_t last_update;
    uint32_t now = hal_millis();
    if (now - last_update < 1000u)
        return;
    last_update = now;
    uint8_t *p = firmware_health_snapshot + 1;
    p[1] = (uint8_t)hal_zigbee_get_network_status();
    p[2] = firmware_health.meter_enabled |
           (firmware_health.meter_stale << 1) |
           (g_macPib.rxOnWhenIdle ? 4 : 0);
    p[3] = firmware_health.last_poll_status;
    put32(p + 4, now);
    put32(p + 8, firmware_health.poll_success);
    put32(p + 12, firmware_health.poll_failure);
    put32(p + 16, firmware_health.parent_loss);
    put32(p + 20, firmware_health.rejoin_start);
    put32(p + 24, firmware_health.rejoin_failure);
    put32(p + 28, firmware_health.rejoin_success);
    put32(p + 32, firmware_health.sample_stalls);
    put32(p + 36, firmware_health.persistence_failures);
    put32(p + 40, firmware_health.meter_enabled ?
          now - firmware_health.sample_time : UINT32_MAX);
    p[44] = (uint8_t)T_evtExcept[0];
    p[45] = (uint8_t)(T_evtExcept[0] >> 8);
    p[46] = (uint8_t)T_evtExcept[1];
    p[47] = 0;
}
