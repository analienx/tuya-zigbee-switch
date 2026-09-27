#ifndef APP_H
#define APP_H

#include <stdbool.h>

void app_init(void);
void app_task(void);
/* False defers a deliberate reboot until pending application state is saved. */
bool app_prepare_reboot(void);

#endif // APP_H
