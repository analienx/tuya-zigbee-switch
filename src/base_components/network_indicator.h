#ifndef _NETWORK_INDICATOR_H_
#define _NETWORK_INDICATOR_H_

#include "hal/tasks.h"
#include "hal/zigbee.h"
#include "led.h"
#include <stdbool.h>
#include <stdint.h>

/* Blink policy while not joined (owner request 2026-11-19), every board:
 * - pairing (factory-new steering): fast blink for exactly as long as the
 *   radio is steering; it stops when steering ends and restarts with the
 *   next steering attempt;
 * - network lost (commissioned device rejoining): slow blink for a few
 *   minutes when a rejoin attempt the platform can see starts, then quiet;
 *   a later reported attempt blinks again only after a quiet gap. On Telink
 *   the SDK runs its rejoin backoff retries internally, so a new blink comes
 *   with each rejoin sequence the firmware starts (boot, rescue reboot,
 *   Router one-shot), not with every SDK retry.
 * Otherwise a disconnected device stays quiet: the relay LED keeps showing
 * the relay state and a dedicated status LED is off. A relay sync that
 * interrupts a blink is repaired at the next status or activity report. */
#define NETWORK_INDICATOR_PAIRING_ON_MS       200
#define NETWORK_INDICATOR_PAIRING_OFF_MS      200
#define NETWORK_INDICATOR_REJOIN_ON_MS        1000
#define NETWORK_INDICATOR_REJOIN_OFF_MS       1000
#define NETWORK_INDICATOR_REJOIN_BLINK_MS     (3u * 60u * 1000u)
/* Minimum time from one rejoin blink's start to the next one's start. */
#define NETWORK_INDICATOR_REJOIN_REPEAT_MS    (10u * 60u * 1000u)

typedef enum {
    NETWORK_INDICATOR_QUIET   = 0,
    NETWORK_INDICATOR_PAIRING = 1,
    NETWORK_INDICATOR_REJOIN  = 2,
} network_indicator_blink_t;

typedef struct {
    led_t *                   leds[4];
    // Extra LEDs that blink in phase with leds[] only during a join blink.
    led_t *                   search_leds[4];
    bool                      has_dedicated_led;
    bool                      manual_state_when_connected;

    network_indicator_blink_t blink;
    bool                      rejoin_blinked;  // this outage
    uint32_t                  rejoin_blink_started_ms;
    hal_task_t                rejoin_stop_task;
    // Called when a join blink ends: the owner re-syncs relay LEDs and, if
    // still joined (a rejoin started while joined), the status LED.
    void (*on_quiet)(void);
} network_indicator_t;

void network_indicator_connected(network_indicator_t *indicator);

void network_indicator_from_manual_state(network_indicator_t *indicator);

void network_indicator_commission_success(network_indicator_t *indicator);

/* Network status is not joined. Never starts a blink by itself. */
void network_indicator_not_connected(network_indicator_t *indicator);

/* What the radio is doing (see hal_register_on_join_activity_callback). */
void network_indicator_join_activity(network_indicator_t *indicator,
                                     hal_zigbee_join_activity_t activity);

#endif
