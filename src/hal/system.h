#ifndef _HAL_SYSTEM_H_
#define _HAL_SYSTEM_H_

#ifdef BSEED_MAINS_CLIENT
#include <stdbool.h>
#endif

/**
 * Reset the system/microcontroller
 */
void __attribute__((noreturn)) hal_system_reset(void);

void hal_factory_reset(void);

#ifdef BSEED_MAINS_CLIENT
/** Reset Zigbee role/network state while preserving application NVM. */
bool hal_role_change_reset(void);
#endif

#endif /* _HAL_SYSTEM_H_ */
