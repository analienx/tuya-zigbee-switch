#include "network_indicator.h"
#include "hal/timer.h"
#include <stddef.h>

static void stop_set(led_t *const *set) {
    for (int i = 0; i < 4 && set[i] != NULL; i++)
        led_stop(set[i]);
}

static void cancel_rejoin_stop(network_indicator_t *indicator) {
    if (indicator->rejoin_stop_task.handler != NULL)
        hal_tasks_unschedule(&indicator->rejoin_stop_task);
}

/* End any join blink: every LED dark, then the owner re-syncs relay LEDs. */
static void blink_stop(network_indicator_t *indicator) {
    bool was_blinking = indicator->blink != NETWORK_INDICATOR_QUIET;

    cancel_rejoin_stop(indicator);
    indicator->blink = NETWORK_INDICATOR_QUIET;
    if (!was_blinking)
        return;

    stop_set(indicator->leds);
    stop_set(indicator->search_leds);
    if (indicator->on_quiet != NULL)
        indicator->on_quiet();
}

static void rejoin_stop_handler(void *arg) {
    network_indicator_t *indicator = (network_indicator_t *)arg;

    if (indicator->blink == NETWORK_INDICATOR_REJOIN)
        blink_stop(indicator);
}

/* Restart the whole set together so every LED flashes in phase. */
static void blink_start(network_indicator_t *indicator,
                        network_indicator_blink_t mode, uint16_t on_ms,
                        uint16_t off_ms) {
    led_t *const *sets[2] = { indicator->leds, indicator->search_leds };

    cancel_rejoin_stop(indicator);
    indicator->blink = mode;
    for (int s = 0; s < 2; s++)
        stop_set(sets[s]);
    for (int s = 0; s < 2; s++)
        for (int i = 0; i < 4 && sets[s][i] != NULL; i++)
            led_blink(sets[s][i], on_ms, off_ms, LED_BLINK_FOREVER);
}

void network_indicator_connected(network_indicator_t *indicator) {
    cancel_rejoin_stop(indicator);
    indicator->blink          = NETWORK_INDICATOR_QUIET;
    indicator->rejoin_blinked = false;
    network_indicator_from_manual_state(indicator);
    // The caller re-syncs these LEDs to their relay state.
    stop_set(indicator->search_leds);
}

void network_indicator_from_manual_state(network_indicator_t *indicator) {
    led_t **led = indicator->leds;

    while (*led != NULL && (led - indicator->leds) < 4) {
        (*led)->blink_times_left = 0;
        if (indicator->has_dedicated_led) {
            if (indicator->manual_state_when_connected) {
                led_on(*led);
            } else {
                led_off(*led);
            }
        }
        led++;
    }
}

void network_indicator_commission_success(network_indicator_t *indicator) {
    led_t **led = indicator->leds;

    while (*led != NULL && (led - indicator->leds) < 4) {
        led_blink(*led, 500, 500, 7);
        led++;
    }
}

void network_indicator_not_connected(network_indicator_t *indicator) {
    if (indicator->blink != NETWORK_INDICATOR_QUIET)
        return;  // a join blink owns the LEDs

    // Quiet while disconnected: a dedicated status LED is dark. Relay
    // indicator LEDs are left to the relay state.
    if (indicator->has_dedicated_led)
        stop_set(indicator->leds);
}

void network_indicator_join_activity(network_indicator_t *indicator,
                                     hal_zigbee_join_activity_t activity) {
    switch (activity) {
    case HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING:
        if (indicator->blink != NETWORK_INDICATOR_PAIRING)
            blink_start(indicator, NETWORK_INDICATOR_PAIRING,
                        NETWORK_INDICATOR_PAIRING_ON_MS,
                        NETWORK_INDICATOR_PAIRING_OFF_MS);
        break;
    case HAL_ZIGBEE_JOIN_ACTIVITY_IDLE:
        if (indicator->blink == NETWORK_INDICATOR_PAIRING)
            blink_stop(indicator);
        break;
    case HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING: {
        if (indicator->blink != NETWORK_INDICATOR_QUIET)
            break;  // already blinking for this attempt
        uint32_t now = hal_millis();
        if (indicator->rejoin_blinked &&
            (uint32_t)(now - indicator->rejoin_blink_started_ms) <
            NETWORK_INDICATOR_REJOIN_REPEAT_MS)
            break;  // retried too soon after the last blink: stay quiet
        indicator->rejoin_blinked          = true;
        indicator->rejoin_blink_started_ms = now;
        blink_start(indicator, NETWORK_INDICATOR_REJOIN,
                    NETWORK_INDICATOR_REJOIN_ON_MS,
                    NETWORK_INDICATOR_REJOIN_OFF_MS);
        // led_blink() pattern: never re-init a task that may still be linked.
        if (indicator->rejoin_stop_task.handler != rejoin_stop_handler) {
            indicator->rejoin_stop_task.handler = rejoin_stop_handler;
            indicator->rejoin_stop_task.arg     = indicator;
            hal_tasks_init(&indicator->rejoin_stop_task);
        }
        hal_tasks_schedule(&indicator->rejoin_stop_task,
                           NETWORK_INDICATOR_REJOIN_BLINK_MS);
        break;
    }
    default:
        break;
    }
}
