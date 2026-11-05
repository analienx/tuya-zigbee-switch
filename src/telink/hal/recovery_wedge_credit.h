#ifndef BSEED_RECOVERY_WEDGE_CREDIT_H
#define BSEED_RECOVERY_WEDGE_CREDIT_H

#include <stdbool.h>
#include <stdint.h>

/* Call regularly while the SDK is known to be wedged. Credit is earned only
 * in that state, in persisted six-hour chunks. A successful consume writes
 * the exhausted credit before the caller schedules a reboot. */
bool bseed_wedge_credit_tick(uint32_t now_ms, bool wedged);
bool bseed_wedge_credit_consume(void);

#endif
