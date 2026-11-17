"""Run the real LED, network indicator and stub scheduler for the BSEED
search blink: status and relay indicator LEDs flash in phase while not
joined, and the relay LED is released to the relay on join."""
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
void hal_gpio_set(hal_gpio_pin_t pin) { pin_level[pin] = 1; }
void hal_gpio_clear(hal_gpio_pin_t pin) { pin_level[pin] = 0; }

static void step(uint32_t ms) {
    for (uint32_t i = 0; i < ms; i += 10) { now_ms += 10; stub_tasks_poll(); }
}

int main(void) {
    led_t status = { .pin = 1, .on_high = 1 };
    led_t relay  = { .pin = 2, .on_high = 1 };
    network_indicator_t ind = {
        .leds = { &status, NULL, NULL, NULL },
        .search_leds = { &relay, NULL, NULL, NULL },
        .has_dedicated_led = true,
        .manual_state_when_connected = 1,
    };

    /* Searching: both LEDs change together on every half period. */
    network_indicator_not_connected(&ind);
    for (int i = 0; i < 6; i++) {
        assert(pin_level[1] == pin_level[2]);
        uint8_t before = pin_level[1];
        step(500);
        assert(pin_level[1] != before && pin_level[1] == pin_level[2]);
    }

    /* A relay sync outside this module cancelled the relay blink mid-phase.
     * The next status change restarts the whole set in phase. */
    step(250);
    led_on(&relay);
    network_indicator_not_connected(&ind);
    for (int i = 0; i < 4; i++) {
        step(500);
        assert(pin_level[1] == pin_level[2]);
    }

    /* Already in phase: a repeated not-joined report must not restart, so
     * the toggle still lands on the original half-period boundary. */
    uint8_t level = pin_level[1];
    step(250);
    network_indicator_not_connected(&ind);
    assert(pin_level[1] == level);
    step(250);
    assert(pin_level[1] != level && pin_level[1] == pin_level[2]);

    /* Joined: status LED follows manual state, relay LED stops blinking and
     * stays where the relay sync leaves it. */
    network_indicator_connected(&ind);
    assert(relay.blink_times_left == 0 && status.blink_times_left == 0);
    led_off(&relay);
    uint8_t s = pin_level[1], r = pin_level[2];
    step(2000);
    assert(pin_level[1] == s && pin_level[2] == r && s == 1 && r == 0);

    /* Kicked again: both start blinking together. */
    network_indicator_not_connected(&ind);
    assert(pin_level[1] == 1 && pin_level[2] == 1);
    step(500);
    assert(pin_level[1] == 0 && pin_level[2] == 0);

    /* Boards without search LEDs keep the original single-LED behavior. */
    led_t only = { .pin = 3, .on_high = 1 };
    network_indicator_t plain = {
        .leds = { &only, NULL, NULL, NULL },
        .has_dedicated_led = true,
        .manual_state_when_connected = 1,
    };
    network_indicator_not_connected(&plain);
    assert(only.blink_times_left == LED_BLINK_FOREVER);

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


def test_search_leds_blink_in_phase_and_release_on_join(tmp_path):
    binary = tmp_path / 'search-leds'
    subprocess.run(
        ['cc', '-std=c99', '-Wall', '-Werror', '-DHAL_STUB',
         '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary),
         '-lpthread'],
        input=CODE, text=True, check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=10)


def test_search_leds_are_bseed_only():
    parser = (ROOT / 'src/device_config/config_parser.c').read_text()
    relay = (ROOT / 'src/zigbee/relay_cluster.c').read_text()
    makefile = (ROOT / 'src/telink/Makefile').read_text()
    assert '#ifdef BSEED_SEARCH_LEDS\n            else {' in parser
    assert '#ifdef BSEED_SEARCH_LEDS\n    // The network-search blink' in relay
    assert 'DEVICE_DEFS += -DBSEED_SEARCH_LEDS' in makefile
    for script in ('build_bseed_ts011f_pm_v8.sh', 'build_bseed_mains_client.sh',
                   'build_bseed_ts011f_nonpm_router.sh'):
        build = (ROOT / 'make_scripts' / script).read_text()
        common = build.split('COMMON_ARGS=(', 1)[1].split('\n)', 1)[0]
        assert 'BSEED_SEARCH_LEDS=1' in common, script
