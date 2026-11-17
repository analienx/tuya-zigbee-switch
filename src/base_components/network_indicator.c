#include "network_indicator.h"
#include <stddef.h>

void network_indicator_connected(network_indicator_t *indicator) {
    network_indicator_from_manual_state(indicator);
    // The caller re-syncs these LEDs to their relay state.
    for (int i = 0; i < 4 && indicator->search_leds[i] != NULL; i++)
        led_stop(indicator->search_leds[i]);
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

static bool all_blinking_forever(led_t *const *set) {
    for (int i = 0; i < 4 && set[i] != NULL; i++) {
        if (set[i]->blink_times_left != LED_BLINK_FOREVER)
            return false;
    }
    return true;
}

static void search_leds_restart(network_indicator_t *indicator) {
    led_t *const *sets[2] = { indicator->leds, indicator->search_leds };

    for (int s = 0; s < 2; s++)
        for (int i = 0; i < 4 && sets[s][i] != NULL; i++)
            led_stop(sets[s][i]);
    for (int s = 0; s < 2; s++)
        for (int i = 0; i < 4 && sets[s][i] != NULL; i++)
            led_blink(sets[s][i], 500, 500, LED_BLINK_FOREVER);
}

void network_indicator_not_connected(network_indicator_t *indicator) {
    led_t **led = indicator->leds;

    if (indicator->search_leds[0] != NULL) {
        // Restart the whole set together so every LED flashes in phase.
        if (!all_blinking_forever(indicator->leds) ||
            !all_blinking_forever(indicator->search_leds))
            search_leds_restart(indicator);
        return;
    }

    while (*led != NULL && (led - indicator->leds) < 4) {
        if ((*led)->blink_times_left != LED_BLINK_FOREVER) {
            led_blink(*led, 500, 500, LED_BLINK_FOREVER);
        }
        led++;
    }
}
