"""Exercise joined callbacks and LED restoration without reporting on CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, name, code):
    binary = tmp_path / name
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DQUIET_JOIN_BLINK',
                    '-DBSEED_SEARCH_LEDS', '-ffunction-sections', '-fdata-sections',
                    '-Wl,--gc-sections', '-I', str(ROOT / 'src'),
                    '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_repeated_joined_callback_preserves_pending_rejoin_and_avoids_reports(tmp_path):
    parser = (ROOT / 'src/device_config/config_parser.c').read_text()
    callback = 'void network_indicator_on_network_status_change(' + parser.split(
        'void network_indicator_on_network_status_change(', 1)[1].split('void peripherals_init()', 1)[0]
    prelude = r'''
#include <assert.h>
#include <stdio.h>
#include "base_components/led.c"
#include "base_components/network_indicator.c"
network_indicator_t network_indicator;
static struct {hal_gpio_pin_t pin;} battery = {HAL_INVALID_PIN};
static unsigned switch_updates, relay_updates;
void update_switch_clusters(void) { switch_updates++; }
void update_relay_clusters(void) { relay_updates++; }
void hal_gpio_set(hal_gpio_pin_t pin) { (void)pin; }
void hal_gpio_clear(hal_gpio_pin_t pin) { (void)pin; }
void hal_tasks_init(hal_task_t *task) { (void)task; }
void hal_tasks_schedule(hal_task_t *task, uint32_t ms) { (void)task; (void)ms; }
void hal_tasks_unschedule(hal_task_t *task) { (void)task; }
uint32_t hal_millis(void) { return 5000; }
'''
    cases = r'''
int main(void) {
    network_indicator_on_network_status_change(HAL_ZIGBEE_NETWORK_JOINED);
    network_indicator_join_activity(&network_indicator, HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING);
    for (int i = 0; i < 3; i++)
        network_indicator_on_network_status_change(HAL_ZIGBEE_NETWORK_JOINED);
    assert(network_indicator.rejoin_pending && !network_indicator.rejoin_blinked);
    assert(switch_updates == 1 && relay_updates == 1);
    network_indicator_on_network_status_change(HAL_ZIGBEE_NETWORK_JOINING);
    assert(network_indicator.blink == NETWORK_INDICATOR_REJOIN);
    network_indicator_on_network_status_change(HAL_ZIGBEE_NETWORK_JOINED);
    assert(network_indicator.blink == NETWORK_INDICATOR_QUIET);
    assert(!network_indicator.rejoin_blinked && !network_indicator.rejoin_pending);
    network_indicator_on_network_status_change(HAL_ZIGBEE_NETWORK_JOINED);
    assert(switch_updates == 2 && relay_updates == 2);
    return 0;
}
'''
    _run(tmp_path, 'led-status', prelude + callback + cases)


def test_relay_led_restore_does_not_notify_attributes_or_touch_relay(tmp_path):
    code = r'''
#include <assert.h>
#include "zigbee/relay_cluster.c"
static unsigned reports, led_writes;
void led_on(led_t *led) { led->on = 1; led_writes++; }
void led_off(led_t *led) { led->on = 0; led_writes++; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at; reports++;
}
int main(void) {
    relay_t relay = {.on = 1};
    led_t led = {0};
    zigbee_relay_cluster cluster = {.relay = &relay, .indicator_led = &led,
        .indicator_led_mode = ZCL_ONOFF_INDICATOR_MODE_SAME};
    relay_cluster_by_endpoint[2] = &cluster;
    update_relay_indicator_leds();
    assert(led.on && cluster.indicator_state && relay.on && led_writes == 1 && reports == 0);
    relay.on = 0;
    update_relay_indicator_leds();
    assert(!led.on && !cluster.indicator_state && led_writes == 2 && reports == 0);
    led.blink_times_left = LED_BLINK_FOREVER;
    update_relay_indicator_leds();
    assert(led_writes == 2 && reports == 0);
    led.blink_times_left = 0;
    sync_indicator_led(&cluster);
    assert(led_writes == 3 && reports == 1);
    return 0;
}
'''
    _run(tmp_path, 'led-relay-restore', code)
