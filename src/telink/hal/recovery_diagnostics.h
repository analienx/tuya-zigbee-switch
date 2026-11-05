#ifndef BSEED_RECOVERY_DIAGNOSTICS_H
#define BSEED_RECOVERY_DIAGNOSTICS_H

#include <stdbool.h>
#include <stdint.h>

/* Persist only a small non-sensitive rescue reason before a deliberate
 * checkpoint reboot. This is not a log: no network addresses, keys, counters
 * or high-frequency writes. Item 72 is independent of the existing budget. */
typedef enum {
    BSEED_RESCUE_REASON_NONE               = 0,
    BSEED_RESCUE_REASON_ROUTER_ORPHAN      = 1,
    BSEED_RESCUE_REASON_ROUTER_BACKOFF     = 2,
    BSEED_RESCUE_REASON_ROUTER_NOT_JOINED  = 3,
    BSEED_RESCUE_REASON_ROUTER_ISOLATED    = 4,
    BSEED_RESCUE_REASON_CLIENT_BACKOFF     = 5,
    BSEED_RESCUE_REASON_CLIENT_QUEUE_STALL = 6,
} bseed_rescue_reason_t;

bool bseed_recovery_diag_record(bseed_rescue_reason_t reason,
                                uint8_t sdk_state);
bool bseed_recovery_diag_last(uint8_t *reason, uint8_t *sdk_state);

#endif
