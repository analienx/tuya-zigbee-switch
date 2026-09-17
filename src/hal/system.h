#ifndef _HAL_SYSTEM_H_
#define _HAL_SYSTEM_H_

#include <stdbool.h>

/**
 * Reset the system/microcontroller
 */
void __attribute__((noreturn)) hal_system_reset(void);

void hal_factory_reset(void);

/** Reset Zigbee role/network state while preserving application NVM. */
bool hal_role_change_reset(void);

#endif /* _HAL_SYSTEM_H_ */
