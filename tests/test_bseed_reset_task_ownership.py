"""Reset/reboot rescheduling must keep exactly one owned pending event."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_reset_tasks_initialize_once_and_reschedule_by_replacement(tmp_path):
    code = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <setjmp.h>
#include "hal/nvm.h"
#include "stub/hal/tasks.c"

static uint32_t now_ms;
uint32_t hal_millis(void) { return now_ms; }

static int prepare_ok;
bool app_prepare_reboot(void) { return prepare_ok != 0; }
hal_nvm_status_t hal_nvm_clear_all(void) { return HAL_NVM_SUCCESS; }
void hal_factory_reset(void) {}
static jmp_buf reset_target;
void hal_system_reset(void) { longjmp(reset_target, 1); }

#include "device_config/reset.c"

static int active_for(hal_task_t *task) {
    int count = 0;
    for (int i = 0; i < MAX_TASKS; i++)
        if (tasks[i].active && tasks[i].task == task) count++;
    return count;
}

int main(void) {
    schedule_reboot(1000);
    assert(active_for(&reset_task) == 1);
    task_handler_t first = reset_task.handler;

    /* Full-reset replaces the same owned event and handler. */
    schedule_full_reset(2000);
    assert(active_for(&reset_task) == 1);
    assert(reset_task.handler != first);

    /* Reboot replaces it again; no duplicate pending callback. */
    schedule_reboot(3000);
    assert(active_for(&reset_task) == 1);
    assert(reset_task.handler == reboot_handler);

    /* Network reset is a separate task and also replaces itself. */
    schedule_network_reset(1000);
    schedule_network_reset(4000);
    assert(active_for(&network_reset_task) == 1);

    /* Checkpoint retry schedules the same reset task without reinitializing it. */
    prepare_ok = 0;
    reboot_handler(NULL);
    assert(active_for(&reset_task) == 1);
    assert(reset_task.handler == reboot_handler);

    /* Execute the retry through the scheduler, not directly: the handler's
     * replacement must survive retirement of the current callback. */
    hal_tasks_unschedule(&network_reset_task);
    now_ms = 5000;
    stub_tasks_poll();
    assert(active_for(&reset_task) == 1);
    prepare_ok = 1;
    now_ms = 10000;
    if (setjmp(reset_target) == 0) {
        stub_tasks_poll();
        assert(0 && "reset must not return");
    }
    assert(active_for(&reset_task) == 0);

    return 0;
}
'''
    binary = tmp_path / 'reset-task-ownership'
    subprocess.run(
        ['cc', '-std=c99', '-DHAL_STUB',
         '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
         '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary),
         '-lpthread'],
        input=code, text=True, check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=5)
