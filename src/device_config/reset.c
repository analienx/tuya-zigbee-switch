#include "reset.h"
#include "app.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "hal/system.h"
#include "hal/tasks.h"
#include <stdint.h>

static hal_task_t reset_task;
static hal_task_t network_reset_task;
static uint8_t    reset_tasks_initialized;

static void ensure_reset_tasks_initialized(void) {
    if (reset_tasks_initialized)
        return;

    hal_tasks_init(&reset_task);
    hal_tasks_init(&network_reset_task);
    reset_tasks_initialized = 1;
}

__attribute__((noreturn)) void reset_all() {
    printf("RESET ALL!\r\n");
    hal_nvm_clear_all();
    hal_factory_reset();
    hal_system_reset();
}

void reset_all_handler(void *arg) {
    reset_all();
}

void reboot_handler(void *arg) {
    (void)arg;
    if (!app_prepare_reboot()) {
        hal_tasks_schedule(&reset_task, 5000);
        return;
    }
    hal_system_reset();
}

static void network_reset_handler(void *arg) {
    (void)arg;
    if (!app_prepare_reboot()) {
        hal_tasks_schedule(&network_reset_task, 5000);
        return;
    }
    hal_factory_reset();
    schedule_reboot(DEFAULT_RESET_DELAY_MS);
}

void schedule_network_reset(uint16_t delay_ms) {
    ensure_reset_tasks_initialized();
    network_reset_task.handler = network_reset_handler;
    hal_tasks_schedule(&network_reset_task,
                       delay_ms != 0 ? delay_ms : DEFAULT_RESET_DELAY_MS);
}

void schedule_full_reset(uint16_t delay_ms) {
    ensure_reset_tasks_initialized();

    /* reset_task is shared intentionally: the newest reboot/full-reset request
     * replaces the previous pending action and its deadline. */
    reset_task.handler = reset_all_handler;
    hal_tasks_schedule(&reset_task,
                       delay_ms != 0 ? delay_ms : DEFAULT_RESET_DELAY_MS);
}

void schedule_reboot(uint16_t delay_ms) {
    ensure_reset_tasks_initialized();
    reset_task.handler = reboot_handler;
    hal_tasks_schedule(&reset_task,
                       delay_ms != 0 ? delay_ms : DEFAULT_RESET_DELAY_MS);
}
