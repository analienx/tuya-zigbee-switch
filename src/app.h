#ifndef APP_H
#define APP_H

#include <stdbool.h>

void app_init(void);
void app_task(void);

/* Called on an SDK-confirmed successful Zigbee join/rejoin, including a
 * Router recovery that never exposed NOT_JOINED to the application loop. */
void app_rearm_network_announcement(void);

/* False defers a deliberate reboot until pending application state is saved. */
bool app_prepare_reboot(void);

#endif // APP_H
