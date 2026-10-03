#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#include "watchdog.h"
#pragma pack(pop)
#include "telink_size_t_hack.h"
#include "hal/firmware_health.h"
#include "hal/timer.h"
#include "patch_sdk/mgmt_rtg_codec.h"

/* SDK counters retain their native widths and wrap. App counters saturate.
* No reports, probes, persistence or route mutations are generated here. */
uint8_t firmware_runtime_snapshot[49] = { 48, 1 };
#if ZB_ROUTER_ROLE
uint8_t         firmware_router_snapshot[49] = { 48, 1 };
static uint8_t  neighbor_high_water, route_high_water, buffer_high_water;
static uint32_t nwk_status_count;
static uint8_t  last_nwk_status  = 0xff;
static uint16_t last_nwk_address = 0xffff;
#endif
static volatile uint32_t max_stack_ticks, max_flash_ticks, flash_operations;
static uint32_t          last_stack_ticks;
static bool     stack_sample_started;
static uint32_t counter_retries, counter_failures;

static void put16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *p, uint32_t value) {
    for (uint8_t i = 0; i < 4; i++)
        p[i] = (uint8_t)(value >> (8 * i));
}

/* Called before restoring IRQs, after flash has completed. Must remain in RAM
* and must not call flash-resident code while flash mapping is unavailable. */
_attribute_ram_code_sec_ void hal_telink_flash_complete(unsigned int ticks) {
    if (ticks > max_flash_ticks)
        max_flash_ticks = ticks;
    if (flash_operations != UINT32_MAX)
        flash_operations++;
}

void hal_telink_stack_service_sample(void) {
    uint32_t now = clock_time();

    if (stack_sample_started) {
        uint32_t elapsed = now - last_stack_ticks;
        if (elapsed > max_stack_ticks)
            max_stack_ticks = elapsed;
    }
    last_stack_ticks     = now;
    stack_sample_started = true;
}

void hal_telink_counter_retry(void) {
    firmware_health_increment(&counter_retries);
}

/* The native callers ignore errors. Fail closed synchronously, including at
 * boot, rather than letting them transmit with an uncommitted counter.
 * Preserve GPIO/relay output; neither reset nor factory-new recovery is safe.
 * There is no automatic watchdog/reboot loop on failing storage. */
void __attribute__((noreturn)) hal_telink_counter_fault(uint8_t status) {
    firmware_health_increment(&counter_failures);
    firmware_runtime_snapshot[4] = status;
    drv_disable_irq();
    rf_set_tx_rx_off();
    wd_stop();
    while (1) {
    }
}

void hal_telink_nwk_status(uint16_t address, uint8_t status) {
#if ZB_ROUTER_ROLE
    last_nwk_address = address;
    last_nwk_status  = status;
    firmware_health_increment(&nwk_status_count);
#else
    (void)address;
    (void)status;
#endif
}

void hal_telink_routing_health_update(void) {
    static uint32_t last_update;
    uint32_t        now = hal_millis();

    if (now - last_update < 1000u)
        return;

    last_update = now;

    /* Native diagnostics/packet allocation may change in IRQ context. Copy
     * only the bounded scalars under the IRQ mask, then encode outside it. */
    uint32_t          irq         = drv_disable_irq();
    sys_diagnostics_t diagnostics = g_sysDiags;
    uint32_t          buffers     = g_mPool.usedNum;
    uint32_t          flash_max = max_flash_ticks, flash_count = flash_operations;
    drv_restore_irq(irq);

    uint8_t *p = firmware_runtime_snapshot + 1;
    p[1] = MAC_IB().rxOnWhenIdle ? 1 : 0;
    put32(p + 4, max_stack_ticks / S_TIMER_CLOCK_1US);
    put32(p + 8, flash_max / S_TIMER_CLOCK_1US);
    put32(p + 12, flash_count);
    put32(p + 16, counter_failures);
    put32(p + 20, counter_retries);
    put32(p + 24, ss_ib.outgoingFrameCounter);
    put32(p + 28, diagnostics.macTxCcaFail);
    put32(p + 32, diagnostics.macRxUcast);
    put32(p + 36, diagnostics.macTxUcast);
    p[40] = diagnostics.lastMessageLQI;
    p[41] = (uint8_t)diagnostics.lastMessageRSSI;
    put32(p + 44, now);

#if ZB_ROUTER_ROLE
    uint8_t neighbors = tl_zbNeighborTableNumGet();
    uint8_t routes    = telink_route_entry_count(false);
    if (neighbors > neighbor_high_water) neighbor_high_water = neighbors;
    if (routes > route_high_water) route_high_water = routes;
    if (buffers > buffer_high_water) buffer_high_water = (uint8_t)buffers;
    p[42] = neighbor_high_water;
    p[43] = route_high_water;

    node_descriptor_t  node;
    power_descriptor_t power;
    af_nodeDescriptorCopy(&node);
    af_powerDescriptorCopy(&power);
    p    = firmware_router_snapshot + 1;
    p[1] = last_nwk_status;
    p[2] = (MAC_IB().rxOnWhenIdle ? 1 : 0) |
           ((node.mac_capability_flag & MAC_CAP_RX_ON_WHEN_IDLE) ? 2 : 0) |
           (power.current_power_mode == POWER_MODE_RECEIVER_SYNCHRONIZED_WHEN_ON_IDLE ? 4 : 0);
    put16(p + 4, last_nwk_address);
    p[6]  = neighbors;
    p[7]  = tl_zbNeighborTableChildEDNumGet();
    p[8]  = TL_ZB_NEIGHBOR_TABLE_SIZE;
    p[9]  = TL_ZB_CHILD_TABLE_SIZE;
    p[10] = routes;
    p[11] = telink_route_entry_count(true);
    p[12] = (uint8_t)ROUTING_TABLE_SIZE;
    p[13] = (uint8_t)buffers;
    p[14] = ZB_BUF_POOL_SIZE;
    p[15] = buffer_high_water;
    put32(p + 16, now);
    put32(p + 20, nwk_status_count);
    put16(p + 24, diagnostics.relayedUcast);
    put16(p + 26, diagnostics.routeDiscInitiated);
    put16(p + 28, diagnostics.macTxUcastRetry);
    put16(p + 30, diagnostics.macTxUcastFail);
    put16(p + 32, diagnostics.packetBufferAllocateFailures);
    put16(p + 34, diagnostics.phytoMACqueuelimitreached);
    put16(p + 36, diagnostics.neighborAdded);
    put16(p + 38, diagnostics.neighborRemoved);
    put16(p + 40, diagnostics.neighborStale);
    put16(p + 42, diagnostics.childMoved);
    put32(p + 44, diagnostics.macRxCrcFail);
#else
    (void)buffers;
#endif
}
