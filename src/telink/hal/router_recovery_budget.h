#ifndef BSEED_ROUTER_RECOVERY_BUDGET_H
#define BSEED_ROUTER_RECOVERY_BUDGET_H

#include <stdbool.h>
#include <stdint.h>

/* Persistently cap automatic Router rescue resets across power cycles.
 * Never delete Zigbee keys, roles, configuration, or metering NVM. */
bool bseed_router_rescue_budget_consume(void);

typedef enum {
    BSEED_BUDGET_CONSUMED,
    BSEED_BUDGET_EXHAUSTED,
    BSEED_BUDGET_STORAGE_ERROR,
} bseed_budget_result_t;
bseed_budget_result_t bseed_router_rescue_budget_try_consume(void);
void bseed_router_rescue_budget_note_good_coordinator(uint32_t now_ms);
void bseed_router_rescue_budget_note_failure(void);

#endif
