#include "network_indicator.h"
#include "hal/timer.h"
#include <stddef.h>

static void stop_set(led_t *const *set) {
    for (int i = 0; i < 4 && set[i] != NULL; i++)
        led_stop(set[i]);
}

static bool all_blinking_forever(led_t *const *set) {
    for (int i = 0; i < 4 && set[i] != NULL; i++) {
        if (set[i]->blink_times_left != LED_BLINK_FOREVER)
            return false;
    }
    return true;
}

static bool any_out_of_phase(network_indicator_t *indicator) {
    return !all_blinking_forever(indicator->leds) ||
           !all_blinking_forever(indicator->search_leds);
}

/* Restart the whole set together so every LED flashes in phase. */
static void restart_sets(network_indicator_t *indicator, uint16_t on_ms,
                         uint16_t off_ms) {
    led_t *const *sets[2] = { indicator->leds, indicator->search_leds };

    for (int s = 0; s < 2; s++)
        stop_set(sets[s]);
    for (int s = 0; s < 2; s++)
        for (int i = 0; i < 4 && sets[s][i] != NULL; i++)
            led_blink(sets[s][i], on_ms, off_ms, LED_BLINK_FOREVER);
}

#ifdef QUIET_JOIN_BLINK
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

static void blink_set(network_indicator_t *indicator) {
    if (indicator->blink == NETWORK_INDICATOR_PAIRING)
        restart_sets(indicator, NETWORK_INDICATOR_PAIRING_ON_MS,
                     NETWORK_INDICATOR_PAIRING_OFF_MS);
    else
        restart_sets(indicator, NETWORK_INDICATOR_REJOIN_ON_MS,
                     NETWORK_INDICATOR_REJOIN_OFF_MS);
}

/* A relay sync outside this module (e.g. a button press on a board whose
 * relay LEDs are its network LEDs) can cancel one LED's blink. Repair the
 * set in phase, without extending a running rejoin blink. */
static void blink_resync(network_indicator_t *indicator) {
    if (any_out_of_phase(indicator))
        blink_set(indicator);
}

static void blink_start(network_indicator_t *indicator,
                        network_indicator_blink_t mode) {
    cancel_rejoin_stop(indicator);
    indicator->blink = mode;
    blink_set(indicator);
}

/* Slow rejoin blink for a few minutes, unless one started too recently. */
static void rejoin_blink_start(network_indicator_t *indicator) {
    uint32_t now = hal_millis();

    if (indicator->rejoin_blinked &&
        (uint32_t)(now - indicator->rejoin_blink_started_ms) <
        NETWORK_INDICATOR_REJOIN_REPEAT_MS)
        return;  // retried too soon after the last blink: stay quiet

    indicator->rejoin_blinked          = true;
    indicator->rejoin_blink_started_ms = now;
    blink_start(indicator, NETWORK_INDICATOR_REJOIN);
    // led_blink() pattern: never re-init a task that may still be linked.
    if (indicator->rejoin_stop_task.handler != rejoin_stop_handler) {
        indicator->rejoin_stop_task.handler = rejoin_stop_handler;
        indicator->rejoin_stop_task.arg     = indicator;
        hal_tasks_init(&indicator->rejoin_stop_task);
    }
    hal_tasks_schedule(&indicator->rejoin_stop_task,
                       NETWORK_INDICATOR_REJOIN_BLINK_MS);
}

#endif

void network_indicator_connected(network_indicator_t *indicator) {
    // Only a real not-joined -> joined transition: platforms repeat JOINED
    // reports (every BDB callback on Telink), which must not end a blink or
    // reset the rejoin repeat gate.
    if (indicator->joined)
        return;

    indicator->joined = true;
#ifdef QUIET_JOIN_BLINK
    cancel_rejoin_stop(indicator);
#endif
    indicator->blink          = NETWORK_INDICATOR_QUIET;
    indicator->rejoin_blinked = false;
    indicator->rejoin_pending = false;
    network_indicator_from_manual_state(indicator);
    // The caller re-syncs these LEDs to their relay state.
    stop_set(indicator->search_leds);
}

void network_indicator_from_manual_state(network_indicator_t *indicator) {
    led_t **led = indicator->leds;

    // A running join blink owns the LEDs; the manual state applies when it
    // ends (connected() and on_quiet both apply it).
    if (indicator->blink != NETWORK_INDICATOR_QUIET)
        return;

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

#ifdef QUIET_JOIN_BLINK
void network_indicator_not_connected(network_indicator_t *indicator) {
    indicator->joined = false;
    if (indicator->blink != NETWORK_INDICATOR_QUIET) {
        blink_resync(indicator);  // a join blink owns the LEDs
        return;
    }
    if (indicator->rejoin_pending) {
        // A rejoin reported while still joined has now left the network.
        indicator->rejoin_pending = false;
        rejoin_blink_start(indicator);
        if (indicator->blink != NETWORK_INDICATOR_QUIET)
            return;
    }

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
            blink_start(indicator, NETWORK_INDICATOR_PAIRING);
        else
            blink_resync(indicator);
        break;
    case HAL_ZIGBEE_JOIN_ACTIVITY_IDLE:
        // No attempt running any more: steering ended or a rejoin completed.
        indicator->rejoin_pending = false;
        if (indicator->blink == NETWORK_INDICATOR_PAIRING)
            blink_stop(indicator);
        break;
    case HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING:
        if (indicator->blink != NETWORK_INDICATOR_QUIET) {
            blink_resync(indicator);  // already blinking for this attempt
            break;
        }
        if (indicator->joined) {
            // Started while still joined (Router one-shot, Client stale
            // parent): the network is not lost yet; blink once it is.
            indicator->rejoin_pending = true;
            break;
        }
        rejoin_blink_start(indicator);
        break;
    default:
        break;
    }
}

#else

/* Original behavior for boards without QUIET_JOIN_BLINK: every network LED
 * (and search LED) blinks for as long as the device is not joined; join
 * activity reports are not used. */
void network_indicator_not_connected(network_indicator_t *indicator) {
    led_t **led = indicator->leds;

    indicator->joined = false;
    if (indicator->search_leds[0] != NULL) {
        if (any_out_of_phase(indicator))
            restart_sets(indicator, 500, 500);
        return;
    }

    while (*led != NULL && (led - indicator->leds) < 4) {
        if ((*led)->blink_times_left != LED_BLINK_FOREVER) {
            led_blink(*led, 500, 500, LED_BLINK_FOREVER);
        }
        led++;
    }
}

void network_indicator_join_activity(network_indicator_t *indicator,
                                     hal_zigbee_join_activity_t activity) {
    (void)indicator;
    (void)activity;
}

#endif
