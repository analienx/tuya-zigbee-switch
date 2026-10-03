#include "hal/timer.h"
#include <stdint.h>

/* SDK elapsed milliseconds preserve fractional ticks, hardware wrap and sleep.
 * This hook runs with IRQs disabled in ev_timer_update. */
static volatile uint32_t elapsed_milliseconds;

void hal_telink_time_update(uint32_t elapsed_ms) {
    elapsed_milliseconds += elapsed_ms;
}

uint32_t hal_millis() {
    return elapsed_milliseconds;
}
