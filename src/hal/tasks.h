#ifndef _HAL_TASKS_H_
#define _HAL_TASKS_H_

#include <stdint.h>

/** Function called when a scheduled task executes */
typedef void (*task_handler_t)(void *arg);

#ifdef HAL_SILABS

#include "zigbee_app_framework_event.h"

typedef sli_zigbee_event_t hal_platfrom_struct_t;

#endif

#ifdef HAL_TELINK

struct ev_timer_event_t;
/* tasks.c checks every field against the SDK without importing its typedefs. */
#pragma pack(push, 1)
typedef struct {
    struct ev_timer_event_t *next;
    int (*cb)(void *);
    void *                   data;
    uint32_t                 timeout;
    uint32_t                 period;
    uint32_t                 curSysTick;
    uint8_t                  resv;
    uint8_t                  isBusy;
    uint8_t                  isRunning;
    uint8_t                  used;
} hal_telink_event_t;
#pragma pack(pop)
typedef struct {
    hal_telink_event_t event;
    uint8_t            dispatching;
    uint8_t            scheduled;
} hal_platfrom_struct_t;

#endif

#ifdef HAL_STUB

typedef struct {
    void *dummy; // Placeholder for stub implementation
} hal_platfrom_struct_t;

#endif

/** Schedulable task for delayed execution (timers, debouncing, periodic
 * actions) */
typedef struct {
    task_handler_t        handler;
    void *                arg;
    hal_platfrom_struct_t platform_struct;
} hal_task_t;

/**
 * Initialize a task for use with the scheduler
 * @param task Task structure to initialize
 */
void hal_tasks_init(hal_task_t *task);

/**
 * Schedule a task to execute after a delay
 * @param task Task to schedule
 * @param delay_ms Delay in milliseconds before execution
 */
void hal_tasks_schedule(hal_task_t *task, uint32_t delay_ms);

/**
 * Cancel a previously scheduled task
 * @param task Task to cancel
 */
void hal_tasks_unschedule(hal_task_t *task);

#endif /* HAL_TASKS_H_ */
