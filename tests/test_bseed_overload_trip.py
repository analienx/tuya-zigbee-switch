"""Overload trips physically de-energize in every relay mode; GPIO proves it."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_protection_uses_physical_state_and_cuts_in_all_modes(tmp_path):
    code = r'''
#include <assert.h>
#include <string.h>
#include "base_components/relay.c"
#include "zigbee/relay_cluster.c"
#include "base_components/overload_protection.c"

#define RELAY_PIN 7
uint8_t allow_simultaneous_latching_pulses;
static uint8_t gpio_level[16];
static unsigned gpio_writes;
void hal_gpio_init_output(hal_gpio_pin_t pin, hal_gpio_pull_t pull, uint8_t v) {
    (void)pull; gpio_level[pin & 15] = v ? 1 : 0; gpio_writes++;
}
void hal_gpio_set(hal_gpio_pin_t pin) { gpio_level[pin & 15] = 1; gpio_writes++; }
void hal_gpio_clear(hal_gpio_pin_t pin) { gpio_level[pin & 15] = 0; gpio_writes++; }
void hal_tasks_init(hal_task_t *t) { (void)t; }
void hal_tasks_schedule(hal_task_t *t, uint32_t ms) { (void)t; (void)ms; }
void hal_tasks_unschedule(hal_task_t *t) { (void)t; }
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    (void)item; (void)size; (void)data; return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    (void)item; (void)size; (void)data; return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}
void led_on(led_t *led) { (void)led; }
void led_off(led_t *led) { (void)led; }

static relay_t relay;
static zigbee_relay_cluster cluster;
static overload_protection_t op;

static void setup(uint8_t mode, uint8_t logical, uint8_t startup) {
    memset(&relay, 0, sizeof relay);
    memset(&cluster, 0, sizeof cluster);
    memset(gpio_level, 0, sizeof gpio_level);
    gpio_writes = 0;
    relay.pin = RELAY_PIN;
    relay.on_high = 1;
    relay.is_latching = 0;
    cluster.relay = &relay;
    cluster.relay_idx = 0;
    cluster.indicator_led = NULL;
    cluster.physical_relay_mode = mode;
    cluster.startup_mode = startup;
    relay_init(&relay, 0);
    relay.on = logical;
    relay_cluster_apply_physical_mode(&cluster);
    overload_protection_init(&op);
}

static uint8_t mode_physical(uint8_t mode, uint8_t logical) {
    if (mode == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON) return 1;
    if (mode == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF) return 0;
    return logical ? 1 : 0;
}

static overload_action_t monitor(uint32_t now_ms, int32_t power_w) {
    return overload_protection_check(&op, now_ms, 23000, 500, power_w,
        relay_cluster_is_physically_on(&cluster), cluster.startup_mode);
}

int main(void) {
    static const uint8_t modes[3] = {ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED,
        ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON,
        ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_OFF};
    /* Hard overload in every mode x logical state; the GPIO double is the verdict. */
    for (unsigned i = 0; i < 3; i++) {
        for (uint8_t logical = 0; logical <= 1; logical++) {
            setup(modes[i], logical, ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF);
            uint8_t expected = mode_physical(modes[i], logical);
            assert(gpio_level[RELAY_PIN] == expected);
            assert(relay_cluster_is_physically_on(&cluster) == expected);
            overload_action_t action = monitor(1000, 4000);
            if (expected) {
                assert(action == OVERLOAD_ACTION_TURN_OFF);
                assert(op.alarm == OVERLOAD_ALARM_PEAK && op.tripped);
                relay_cluster_protection_trip(&cluster);
                assert(gpio_level[RELAY_PIN] == 0);
                assert(relay.on == 0 && cluster.protection_tripped == 1);
                assert(relay_cluster_is_physically_on(&cluster) == 0);
                /* Trip outranks a later detached/Always-on application. */
                cluster.physical_relay_mode = ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON;
                relay_cluster_apply_physical_mode(&cluster);
                assert(gpio_level[RELAY_PIN] == 0);
                /* OFF never rearms. */
                relay_cluster_off(&cluster);
                assert(cluster.protection_tripped == 1 && gpio_level[RELAY_PIN] == 0);
                /* Remote ON/toggle cannot bypass protection or reset retries. */
                relay_cluster_on(&cluster);
                relay_cluster_toggle(&cluster);
                assert(cluster.protection_tripped == 1 && relay.on == 0);
                assert(gpio_level[RELAY_PIN] == 0);
                assert(relay_cluster_is_physically_on(&cluster) == 0);
            } else {
                assert(action == OVERLOAD_ACTION_NONE);
                assert(op.alarm == OVERLOAD_ALARM_NONE && !op.tripped);
                assert(gpio_level[RELAY_PIN] == 0 && cluster.protection_tripped == 0);
            }
        }
    }
    /* Delayed overload with logical OFF but Always-on physical output. */
    setup(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON, 0,
          ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF);
    assert(relay_cluster_is_physically_on(&cluster) == 1);
    assert(monitor(1000, 2600) == OVERLOAD_ACTION_NONE && op.alarm == OVERLOAD_ALARM_POWER);
    assert(monitor(1000 + 31 * 1000, 2600) == OVERLOAD_ACTION_TURN_OFF);
    relay_cluster_protection_trip(&cluster);
    assert(gpio_level[RELAY_PIN] == 0 && relay.on == 0);
    relay_cluster_on(&cluster);
    assert(gpio_level[RELAY_PIN] == 0 && cluster.protection_tripped == 1);
    /* Sustained overload locks out after the bounded reconnects. */
    setup(ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED, 1,
          ZCL_START_UP_ONOFF_SET_ONOFF_TO_ON);
    uint32_t now_ms = 1000;
    for (int cycle = 0; cycle < 6; cycle++) {
        assert(relay_cluster_is_physically_on(&cluster) == 1);
        assert(monitor(now_ms, 4000) == OVERLOAD_ACTION_TURN_OFF);
        relay_cluster_protection_trip(&cluster);
        assert(gpio_level[RELAY_PIN] == 0);
        now_ms += 61 * 1000;
        overload_action_t action = monitor(now_ms, 4000);
        if (cycle < 5) {
            assert(action == OVERLOAD_ACTION_TURN_ON);
            relay_cluster_protection_rearm(&cluster);
            assert(gpio_level[RELAY_PIN] == 1);
        } else {
            assert(action == OVERLOAD_ACTION_NONE);
        }
    }
    assert(op.locked_out == 1 && op.alarm == OVERLOAD_ALARM_LOCKED_OUT);
    assert(op.retry_count == OVERLOAD_MAX_RETRIES);
    assert(gpio_level[RELAY_PIN] == 0);
    /* Lockout survives remote ON/toggle and a physical-mode write. */
    relay_cluster_on(&cluster);
    relay_cluster_toggle(&cluster);
    cluster.physical_relay_mode = ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON;
    relay_cluster_apply_physical_mode(&cluster);
    assert(gpio_level[RELAY_PIN] == 0 && cluster.protection_tripped == 1);
    assert(monitor(now_ms, 4000) == OVERLOAD_ACTION_NONE);
    assert(op.locked_out == 1 && op.retry_count == OVERLOAD_MAX_RETRIES);
    /* Untouched behavior: detached ON drives nothing new; toggle still flips. */
    setup(ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON, 0,
          ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF);
    unsigned writes_before = gpio_writes;
    relay_cluster_on(&cluster);
    assert(relay.on == 1 && gpio_level[RELAY_PIN] == 1 && gpio_writes == writes_before);
    setup(ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED, 0,
          ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF);
    relay_cluster_on(&cluster);
    assert(gpio_level[RELAY_PIN] == 1);
    relay_cluster_toggle(&cluster);
    assert(gpio_level[RELAY_PIN] == 0 && relay.on == 0);
    relay_cluster_toggle(&cluster);
    assert(gpio_level[RELAY_PIN] == 1 && relay.on == 1);
    /* Boot starts unlatched even from stale RAM. */
    setup(ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED, 0,
          ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF);
    hal_zigbee_cluster clusters[4];
    hal_zigbee_endpoint endpoint;
    memset(&endpoint, 0, sizeof endpoint);
    endpoint.endpoint = 2;
    endpoint.clusters = clusters;
    cluster.protection_tripped = 1;
    relay_cluster_add_to_endpoint(&cluster, &endpoint);
    assert(cluster.protection_tripped == 0 && gpio_level[RELAY_PIN] == 0);
    return 0;
}
'''
    binary = tmp_path / 'overload-trip-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DBSEED_PM_B28WRPVX=1',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    elec = (ROOT / 'src/zigbee/electrical_measurement_cluster.c').read_text()
    assert 'relay_cluster_is_physically_on(relay)' in elec
    assert 'relay_cluster_protection_trip(relay)' in elec
    assert 'power, energized, relay->startup_mode' in elec
