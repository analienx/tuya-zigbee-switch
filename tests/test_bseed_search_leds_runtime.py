"""Run the real LED, network indicator and stub scheduler for the join blink
policy (owner request 2026-11-19):

- pairing blinks fast for exactly as long as steering runs;
- a rejoin blinks slowly for a few minutes, then stays quiet, and a later
  rejoin attempt blinks again only after a quiet gap;
- otherwise a disconnected device is quiet: the relay LED keeps the relay
  state and a dedicated status LED is dark;
- every lit LED uses the configured brightness (PWM duty)."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include "stub/hal/tasks.c"
#include "base_components/led.c"
#include "base_components/network_indicator.c"

bool g_machine_mode;
static uint32_t now_ms;
uint32_t hal_millis(void) { return now_ms; }
static uint8_t pin_level[4];
static uint8_t pin_duty[4];
void hal_gpio_set(hal_gpio_pin_t pin) { pin_level[pin] = 1; }
void hal_gpio_clear(hal_gpio_pin_t pin) { pin_level[pin] = 0; }
void hal_gpio_write_duty(hal_gpio_pin_t pin, uint8_t value, uint8_t duty) {
    pin_duty[pin] = duty;
    hal_gpio_write(pin, value);
}
static unsigned quiet_calls;
static void on_quiet(void) { quiet_calls++; }

static void step(uint32_t ms) {
    for (uint32_t i = 0; i < ms; i += 10) { now_ms += 10; stub_tasks_poll(); }
}

/* Both LEDs toggle together every `half` ms for `periods` half periods. */
static void assert_blinking(uint32_t half, int periods) {
    for (int i = 0; i < periods; i++) {
        assert(pin_level[1] == pin_level[2]);
        uint8_t before = pin_level[1];
        step(half);
        assert(pin_level[1] != before && pin_level[1] == pin_level[2]);
    }
}

static void assert_steady(uint32_t ms) {
    uint8_t s = pin_level[1], r = pin_level[2];
    step(ms);
    assert(pin_level[1] == s && pin_level[2] == r);
}

int main(void) {
    led_t status = { .pin = 1, .on_high = 1 };
    led_t relay  = { .pin = 2, .on_high = 1 };
    network_indicator_t ind = {
        .leds = { &status, NULL, NULL, NULL },
        .search_leds = { &relay, NULL, NULL, NULL },
        .has_dedicated_led = true,
        .manual_state_when_connected = 1,
        .on_quiet = on_quiet,
    };

    /* Not joined, nothing running: quiet. The relay LED keeps the relay
     * state, the dedicated status LED is dark. */
    led_on(&status);
    led_on(&relay);
    network_indicator_not_connected(&ind);
    assert(pin_level[1] == 0 && pin_level[2] == 1);
    assert_steady(5000);

    /* Pairing: fast in-phase blink for as long as steering runs. */
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING);
    assert(pin_level[1] == 1 && pin_level[2] == 1);
    assert_blinking(NETWORK_INDICATOR_PAIRING_ON_MS, 40);
    /* A repeated PAIRING report does not restart the phase. */
    uint8_t level = pin_level[1];
    step(100);
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING);
    assert(pin_level[1] == level);
    step(100);
    assert(pin_level[1] != level);
    /* Steering ended: blink stops, relay LEDs handed back to the relay. */
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_IDLE);
    assert(quiet_calls == 1 && ind.blink == NETWORK_INDICATOR_QUIET);
    led_on(&relay);  /* what the relay re-sync does for a relay that is ON */
    assert_steady(60000);
    /* A status change while quiet never starts a blink. */
    network_indicator_not_connected(&ind);
    assert_steady(5000);

    /* The next steering attempt blinks again. */
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING);
    assert_blinking(NETWORK_INDICATOR_PAIRING_ON_MS, 6);

    /* Joined: status LED follows manual state, relay LED is released. */
    network_indicator_connected(&ind);
    assert(relay.blink_times_left == 0 && status.blink_times_left == 0);
    led_off(&relay);
    assert(pin_level[1] == 1 && pin_level[2] == 0);
    assert_steady(2000);

    /* Network lost: a rejoin attempt blinks slowly for a few minutes. */
    quiet_calls = 0;
    uint32_t outage = now_ms;
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    assert(ind.blink == NETWORK_INDICATOR_REJOIN);
    assert_blinking(NETWORK_INDICATOR_REJOIN_ON_MS, 6);
    /* Further attempts during the blink do not extend it. */
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    step(NETWORK_INDICATOR_REJOIN_BLINK_MS - (now_ms - outage) + 20);
    assert(ind.blink == NETWORK_INDICATOR_QUIET && quiet_calls == 1);
    assert(pin_level[1] == 0);  /* status LED dark while disconnected */
    assert_steady(30000);
    /* An attempt soon after stays quiet. */
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    assert(ind.blink == NETWORK_INDICATOR_QUIET);
    assert_steady(30000);
    /* Once the repeat gap has passed, the next attempt blinks again. */
    step(NETWORK_INDICATOR_REJOIN_REPEAT_MS - (now_ms - outage));
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    assert(ind.blink == NETWORK_INDICATOR_REJOIN);
    assert_blinking(NETWORK_INDICATOR_REJOIN_ON_MS, 2);

    /* Rejoined mid-blink: blink and its stop timer end; a new outage blinks
     * at once. */
    network_indicator_connected(&ind);
    assert(ind.blink == NETWORK_INDICATOR_QUIET && !ind.rejoin_blinked);
    led_off(&relay);
    assert_steady(NETWORK_INDICATOR_REJOIN_BLINK_MS + 1000);
    network_indicator_join_activity(&ind, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    assert(ind.blink == NETWORK_INDICATOR_REJOIN);
    network_indicator_connected(&ind);

    /* Boards without search LEDs: pairing blinks the single LED. */
    led_t only = { .pin = 3, .on_high = 1 };
    network_indicator_t plain = {
        .leds = { &only, NULL, NULL, NULL },
        .has_dedicated_led = true,
        .manual_state_when_connected = 1,
    };
    network_indicator_not_connected(&plain);
    assert(only.blink_times_left == 0);
    network_indicator_join_activity(&plain, HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING);
    assert(only.blink_times_left == LED_BLINK_FOREVER);
    network_indicator_join_activity(&plain, HAL_ZIGBEE_JOIN_ACTIVITY_IDLE);
    assert(only.blink_times_left == 0 && pin_level[3] == 0);

    /* Brightness: lit LEDs use the duty, dark LEDs are fully inactive. */
    assert(led_get_brightness() == LED_DEFAULT_BRIGHTNESS);
    led_set_brightness(50);
    led_on(&only);
    assert(pin_level[3] == 1 && pin_duty[3] == 50);
    led_off(&only);
    assert(pin_level[3] == 0 && pin_duty[3] == 100);
    led_t low = { .pin = 0, .on_high = 0 };
    led_on(&low);
    assert(pin_level[0] == 0 && pin_duty[0] == 50);  /* active-low lit */
    led_set_brightness(0);
    assert(led_get_brightness() == LED_MIN_BRIGHTNESS);
    led_set_brightness(200);
    assert(led_get_brightness() == LED_MAX_BRIGHTNESS);
    led_set_brightness(20);
    led_refresh(&low);
    assert(pin_duty[0] == 20);
    led_set_brightness(LED_DEFAULT_BRIGHTNESS);

    /* led_on() mid-blink leaves the timer scheduled; a new blink must cancel
     * it, not re-initialize it (the stub scheduler exits on that). */
    led_t lone = { .pin = 0, .on_high = 1 };
    led_blink(&lone, 500, 500, LED_BLINK_FOREVER);
    step(100);
    led_on(&lone);
    led_blink(&lone, 500, 500, LED_BLINK_FOREVER);
    assert(pin_level[0] == 1);
    step(500);
    assert(pin_level[0] == 0);
    return 0;
}
'''


def test_join_blink_policy_and_brightness(tmp_path):
    binary = tmp_path / 'search-leds'
    subprocess.run(
        ['cc', '-std=c99', '-Wall', '-Werror', '-DHAL_STUB',
         '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary),
         '-lpthread'],
        input=CODE, text=True, check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)


def test_search_leds_are_bseed_only():
    parser = (ROOT / 'src/device_config/config_parser.c').read_text()
    relay = (ROOT / 'src/zigbee/relay_cluster.c').read_text()
    makefile = (ROOT / 'src/telink/Makefile').read_text()
    assert '#ifdef BSEED_SEARCH_LEDS\n            else {' in parser
    assert '#ifdef BSEED_SEARCH_LEDS\n    // The network-search blink' in relay
    assert 'DEVICE_DEFS += -DBSEED_SEARCH_LEDS' in makefile
    assert 'LED_DEFAULT_BRIGHTNESS=50' in makefile
    for script in ('build_bseed_ts011f_pm_v8.sh', 'build_bseed_mains_client.sh',
                   'build_bseed_ts011f_nonpm_router.sh'):
        build = (ROOT / 'make_scripts' / script).read_text()
        common = build.split('COMMON_ARGS=(', 1)[1].split('\n)', 1)[0]
        assert 'BSEED_SEARCH_LEDS=1' in common, script


def test_join_activity_is_reported_where_the_radio_acts():
    net = (ROOT / 'src/telink/hal/zigbee_network.c').read_text()
    # Pairing is announced before steering starts and ends at its terminal
    # outcome; every rejoin start and SDK retry is reported.
    assert net.index('notify_join_activity(HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING)') < \
        net.index('u8 res = bdb_networkSteerStart();')
    assert net.count('notify_join_activity(HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING)') >= 3
    assert net.count('notify_join_activity(HAL_ZIGBEE_JOIN_ACTIVITY_IDLE)') >= 2
    parser = (ROOT / 'src/device_config/config_parser.c').read_text()
    assert 'hal_register_on_join_activity_callback(network_indicator_on_join_activity)' in parser
