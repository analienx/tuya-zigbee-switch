#include "relay.h"
#include "hal/gpio.h"
#include "hal/printf_selector.h"
#include "hal/tasks.h"
#include <stddef.h>

#ifndef RELAY_PULSE_MS
#define RELAY_PULSE_MS    100
#endif

#ifndef PULSE_WAIT_END_MS
#define PULSE_WAIT_END_MS    50
#endif

extern uint8_t allow_simultaneous_latching_pulses;

static relay_t *pulse_relay = NULL;

static void relay_start_latching_pulse(relay_t *relay);
static void relay_end_latching_pulse(relay_t *relay);

static void relay_end_latching_pulse(relay_t *relay) {
    hal_gpio_write(relay->pin, !relay->on_high);
    hal_gpio_write(relay->off_pin, !relay->on_high);
    if (pulse_relay == relay) {
        // If this relay had a pending pulse, mark it as cleared
        pulse_relay = NULL;
    }
}

static void relay_start_latching_pulse(relay_t *relay) {
    hal_gpio_pin_t pin = relay->pending_on ? relay->pin : relay->off_pin;

    if (pulse_relay == NULL || allow_simultaneous_latching_pulses) {
        // Start new pulse
        hal_gpio_write(pin, relay->on_high);
        pulse_relay = relay;
        relay->latching_task.handler = (task_handler_t)relay_end_latching_pulse;
        hal_tasks_schedule(&relay->latching_task, RELAY_PULSE_MS);
    } else {
        printf("relay_start_latching_pulse: another pulse is active\r\n");
        relay->latching_task.handler = (task_handler_t)relay_start_latching_pulse;
        hal_tasks_schedule(&relay->latching_task, PULSE_WAIT_END_MS);
    }
}

void relay_init(relay_t *relay, uint8_t initial_physical_state) {
    relay->latching_task.arg = relay;
    hal_tasks_init(&relay->latching_task);
    relay->pending_on = 0;

    if (relay->is_latching) {
        // Latching coils must never be held energized, and the contact state
        // cannot be inferred by driving a coil: BOTH coil pins first-enable
        // INACTIVE regardless of the persisted policy. The relay cluster
        // issues at most the single serialized policy pulse when it applies
        // the policy. relay->on (virtual state) is left to the startup-mode
        // logic.
        hal_gpio_init_output(relay->pin, HAL_GPIO_PULL_NONE, !relay->on_high);
        hal_gpio_init_output(relay->off_pin, HAL_GPIO_PULL_NONE,
                             !relay->on_high);
        return;
    }

    // Normal relay: first output enable already carries the desired
    // electrical level, computed from on_high so active-low relays behave
    // identically. NOTE: relay->on is the VIRTUAL state and is deliberately
    // not touched here; the startup-mode logic owns it.
    hal_gpio_init_output(relay->pin, HAL_GPIO_PULL_NONE,
                         initial_physical_state ? relay->on_high
                                                : !relay->on_high);
}

void relay_drive_physical(relay_t *relay, uint8_t state) {
    if (relay == NULL) {
        return;
    }

    if (!relay->is_latching) {
        hal_gpio_write(relay->pin, state ? relay->on_high : !relay->on_high);
        return;
    }

    relay->pending_on = state ? 1 : 0;
    relay_end_latching_pulse(relay);
    hal_tasks_unschedule(&relay->latching_task);
    relay_start_latching_pulse(relay);
}

void relay_on(relay_t *relay) {
    if (relay == NULL) {
        return;
    }
    printf("relay_on\r\n");

    relay->on = 1;
    relay_drive_physical(relay, 1);

    if (relay->on_change != NULL) {
        relay->on_change(relay->callback_param, 1);
    }
}

void relay_off(relay_t *relay) {
    if (relay == NULL) {
        return;
    }
    printf("relay_off\r\n");

    relay->on = 0;
    relay_drive_physical(relay, 0);

    if (relay->on_change != NULL) {
        relay->on_change(relay->callback_param, 0);
    }
}

void relay_toggle(relay_t *relay) {
    if (relay == NULL) {
        return;
    }
    printf("relay_toggle\r\n");

    if (relay->on) {
        relay_off(relay);
    } else {
        relay_on(relay);
    }
}
