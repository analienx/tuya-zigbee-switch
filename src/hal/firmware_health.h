#ifndef HAL_FIRMWARE_HEALTH_H
#define HAL_FIRMWARE_HEALTH_H
#include <stdint.h>

/* RAM-only counters; all reset on boot and saturate instead of wrapping. */
typedef struct {
    uint32_t poll_success, poll_failure, parent_loss;
    uint32_t rejoin_start, rejoin_failure, rejoin_success;
    uint32_t sample_stalls, persistence_failures, sample_time;
    uint8_t  meter_enabled, meter_stale, last_poll_status;
} firmware_health_t;

extern firmware_health_t firmware_health;
extern uint8_t           firmware_health_snapshot[49];
void hal_firmware_health_update(void);

static inline void firmware_health_increment(uint32_t *counter) {
    if (*counter != UINT32_MAX)
        (*counter)++;
}

#endif
