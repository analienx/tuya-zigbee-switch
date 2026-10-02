#include "hal/tasks.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

#define CHECK_EVENT_FIELD(field) typedef char check_event_##field[ \
    __builtin_offsetof(hal_telink_event_t, field) == \
    __builtin_offsetof(ev_timer_event_t, field) ? 1 : -1]
typedef char check_event_size[
    sizeof(hal_telink_event_t) == sizeof(ev_timer_event_t) ? 1 : -1];
CHECK_EVENT_FIELD(next);
CHECK_EVENT_FIELD(cb);
CHECK_EVENT_FIELD(data);
CHECK_EVENT_FIELD(timeout);
CHECK_EVENT_FIELD(period);
CHECK_EVENT_FIELD(curSysTick);
CHECK_EVENT_FIELD(resv);
CHECK_EVENT_FIELD(isBusy);
CHECK_EVENT_FIELD(isRunning);
CHECK_EVENT_FIELD(used);

/* Static application events cannot exhaust the SDK timer pool. */
static int _telink_task_wrapper(void *data) {
    hal_task_t *task = (hal_task_t *)data;
    task->platform_struct.dispatching = 1;
    task->platform_struct.scheduled = 0;
    task->handler(task->arg);
    return 0;
}

/* Called by the build-local SDK with IRQs masked before timer cleanup. An ISR
 * rearm between handler return and cleanup therefore cannot be cancelled. */
int hal_telink_task_finish(ev_timer_event_t *event, int result) {
    if (event->cb != _telink_task_wrapper)
        return result;
    hal_task_t *task = (hal_task_t *)event->data;
    task->platform_struct.dispatching = 0;
    return task->platform_struct.scheduled ?
           (int)task->platform_struct.event.period : -1;
}

void hal_tasks_init(hal_task_t *task) {
    memset(&task->platform_struct, 0, sizeof(task->platform_struct));
}

void hal_tasks_schedule(hal_task_t *task, uint32_t delay_ms) {
    /* Zero delay yields instead of recurring in the current SDK traversal.
     * The SDK callback return uses signed milliseconds. */
    if (delay_ms == 0)
        delay_ms = 1;
    if (delay_ms > 0x7FFFFFFFUL)
        delay_ms = 0x7FFFFFFFUL;
    uint32_t irq = drv_disable_irq();
    hal_telink_event_t *event = &task->platform_struct.event;
    event->cb = _telink_task_wrapper;
    event->data = task;
    event->period = delay_ms;
    event->curSysTick = clock_time();
    event->isRunning = 0;
    task->platform_struct.scheduled = 1;
    if (!task->platform_struct.dispatching)
        ev_on_timer((ev_timer_event_t *)event, delay_ms);
    drv_restore_irq(irq);
}

void hal_tasks_unschedule(hal_task_t *task) {
    uint32_t irq = drv_disable_irq();
    task->platform_struct.scheduled = 0;
    if (!task->platform_struct.dispatching)
        ev_unon_timer((ev_timer_event_t *)&task->platform_struct.event);
    drv_restore_irq(irq);
}
