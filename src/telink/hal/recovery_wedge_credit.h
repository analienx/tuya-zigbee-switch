#ifndef BSEED_RECOVERY_WEDGE_CREDIT_H
#define BSEED_RECOVERY_WEDGE_CREDIT_H

#include <stdbool.h>
#include <stdint.h>

/* Call regularly while the SDK is known to be wedged. Credit is earned only
 * in that state, in persisted six-hour chunks. Credit is cumulative across
 * distinct wedge episodes and verified healthy intervals; partial chunks are
 * discarded when an episode ends. A successful consume clears persisted
 * credit before the caller schedules a reboot. The caller must detect a new
 * wedge independently before consuming any previously accumulated credit. */
bool bseed_wedge_credit_tick(uint32_t now_ms, bool wedged);
bool bseed_wedge_credit_consume(void);

#endif
