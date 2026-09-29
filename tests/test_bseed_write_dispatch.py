"""Rejected foundation writes dispatch nothing: no callback, NVM write, or reboot."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

PREDICATE_CODE = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/zcl_write_dispatch.c"

static uint8_t onoff_value;
static uint8_t mode_value;
static uint8_t cfg_storage[130];
static hal_zigbee_attribute onoff_attrs[4];
static hal_zigbee_attribute basic_attrs[1];
static hal_zigbee_attribute ota_attrs[1];
static hal_zigbee_cluster clusters[3];
static hal_zigbee_endpoint eps[1];

int main(void) {
    onoff_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_ONOFF, ZCL_DATA_TYPE_BOOLEAN,
        ATTR_READONLY, sizeof onoff_value, &onoff_value};
    onoff_attrs[1] = (hal_zigbee_attribute){ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
        ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, sizeof mode_value, &mode_value};
    onoff_attrs[2] = (hal_zigbee_attribute){ZCL_ATTR_START_UP_ONOFF,
        ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, sizeof mode_value, &mode_value};
    onoff_attrs[3] = (hal_zigbee_attribute){0x4004, ZCL_DATA_TYPE_UINT8,
        ATTR_WRITABLE, 1, 0};
    basic_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_BASIC_DEVICE_CONFIG,
        ZCL_DATA_TYPE_LONG_CHAR_STR, ATTR_WRITABLE, sizeof cfg_storage, cfg_storage};
    ota_attrs[0] = (hal_zigbee_attribute){0x0000, ZCL_DATA_TYPE_UINT8,
        ATTR_WRITABLE, sizeof onoff_value, &onoff_value};
    clusters[0] = (hal_zigbee_cluster){ZCL_CLUSTER_ON_OFF, 1, 4, onoff_attrs, 0};
    clusters[1] = (hal_zigbee_cluster){ZCL_CLUSTER_BASIC, 1, 1, basic_attrs, 0};
    clusters[2] = (hal_zigbee_cluster){ZCL_CLUSTER_OTA_BOOTLOAD, 1, 1, ota_attrs, 0};
    eps[0] = (hal_zigbee_endpoint){2, 0, 0, 0, 3, clusters};
    assert(zcl_write_record_applied(2, ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_DATA_TYPE_ENUM8, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF, ZCL_DATA_TYPE_BOOLEAN, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_DATA_TYPE_UINT16, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_ON_OFF, 0x1234,
        ZCL_DATA_TYPE_UINT8, eps, 1));
    assert(!zcl_write_record_applied(9, ZCL_CLUSTER_ON_OFF,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE, ZCL_DATA_TYPE_ENUM8, eps, 1));
    assert(!zcl_write_record_applied(2, 0x9999, ZCL_ATTR_ONOFF,
        ZCL_DATA_TYPE_BOOLEAN, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_ON_OFF, 0x4004,
        ZCL_DATA_TYPE_UINT8, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_OTA_BOOTLOAD, 0x0000,
        ZCL_DATA_TYPE_UINT8, eps, 1));
    assert(zcl_write_record_applied(2, ZCL_CLUSTER_BASIC,
        ZCL_ATTR_BASIC_DEVICE_CONFIG, ZCL_DATA_TYPE_LONG_CHAR_STR, eps, 1));
    assert(!zcl_write_record_applied(2, ZCL_CLUSTER_BASIC,
        ZCL_ATTR_BASIC_DEVICE_CONFIG, ZCL_DATA_TYPE_CHAR_STR, eps, 1));
    return 0;
}
'''

DISPATCH_CODE = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/zcl_write_dispatch.c"
#include "zigbee/general_commands.c"
#include "base_components/relay.c"
#define nv_config_buffer relay_nv_config_buffer
#include "zigbee/relay_cluster.c"
#undef nv_config_buffer
#include "device_config/config_nv.c"
#include "zigbee/basic_cluster.c"

#define RELAY_PIN 7
uint8_t allow_simultaneous_latching_pulses;
uint8_t g_multi_press_reset_count;
/* The config fixture contains no GPIOs; pin parsing has separate coverage. */
hal_gpio_pin_t hal_gpio_parse_pin(const char *s) {(void)s; return HAL_INVALID_PIN;}
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
static uint8_t nvm_config[sizeof(device_config_str_t)];
static unsigned nvm_writes;
static unsigned config_item_writes;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (item == NV_ITEM_DEVICE_CONFIG && size <= sizeof nvm_config) {
        memcpy(data, nvm_config, size);
        return HAL_NVM_SUCCESS;
    }
    return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    (void)size; (void)data; nvm_writes++;
    if (item == NV_ITEM_DEVICE_CONFIG) {
        config_item_writes++;
    }
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}
void led_on(led_t *led) { (void)led; }
void led_off(led_t *led) { (void)led; }
static unsigned reboots;
void schedule_reboot(uint16_t delay_ms) { (void)delay_ms; reboots++; }
network_indicator_t network_indicator;
void network_indicator_from_manual_state(network_indicator_t *indicator) { (void)indicator; }
void device_params_set_multi_press_reset_count(uint8_t value) { (void)value; }

/* Recording doubles: rejected records must never reach these clusters. */
static unsigned switch_calls, cover_calls, cover_switch_calls, metering_calls, elec_calls;
void switch_cluster_callback_attr_write_trampoline(uint8_t endpoint, uint16_t attribute_id) {
    (void)endpoint; (void)attribute_id; switch_calls++;
}
void cover_cluster_callback_attr_write_trampoline(uint8_t endpoint, uint16_t attribute_id) {
    (void)endpoint; (void)attribute_id; cover_calls++;
}
void cover_switch_cluster_callback_attr_write_trampoline(uint8_t endpoint, uint16_t attribute_id) {
    (void)endpoint; (void)attribute_id; cover_switch_calls++;
}
void metering_cluster_callback_attr_write_trampoline(uint8_t endpoint, uint16_t attribute_id) {
    (void)endpoint; (void)attribute_id; metering_calls++;
}
void electrical_measurement_cluster_callback_attr_write_trampoline(uint8_t endpoint, uint16_t attribute_id) {
    (void)endpoint; (void)attribute_id; elec_calls++;
}

static relay_t relay;
static zigbee_relay_cluster cluster;
static hal_zigbee_attribute onoff_attrs[3];
static hal_zigbee_attribute basic_attrs[1];
static hal_zigbee_cluster clusters[2];
static hal_zigbee_endpoint eps[1];

static void setup(void) {
    memset(&relay, 0, sizeof relay);
    memset(&cluster, 0, sizeof cluster);
    memset(gpio_level, 0, sizeof gpio_level);
    gpio_writes = 0;
    nvm_writes = 0;
    config_item_writes = 0;
    reboots = 0;
    relay.pin = RELAY_PIN;
    relay.on_high = 1;
    relay.is_latching = 0;
    cluster.relay = &relay;
    cluster.relay_idx = 0;
    cluster.indicator_led = NULL;
    cluster.endpoint = 2;
    cluster.physical_relay_mode = ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED;
    cluster.startup_mode = ZCL_START_UP_ONOFF_SET_ONOFF_TO_OFF;
    relay_cluster_by_endpoint[2] = &cluster;
    relay_init(&relay, 0);
    relay.on = 0;
    relay_cluster_apply_physical_mode(&cluster);
    onoff_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_ONOFF, ZCL_DATA_TYPE_BOOLEAN,
        ATTR_READONLY, sizeof relay.on, &relay.on};
    onoff_attrs[1] = (hal_zigbee_attribute){ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
        ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, sizeof cluster.physical_relay_mode,
        &cluster.physical_relay_mode};
    onoff_attrs[2] = (hal_zigbee_attribute){ZCL_ATTR_START_UP_ONOFF,
        ZCL_DATA_TYPE_ENUM8, ATTR_WRITABLE, sizeof cluster.startup_mode,
        &cluster.startup_mode};
    basic_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_BASIC_DEVICE_CONFIG,
        ZCL_DATA_TYPE_LONG_CHAR_STR, ATTR_WRITABLE, sizeof device_config_str,
        (uint8_t *)&device_config_str};
    clusters[0] = (hal_zigbee_cluster){ZCL_CLUSTER_ON_OFF, 1, 3, onoff_attrs, 0};
    clusters[1] = (hal_zigbee_cluster){ZCL_CLUSTER_BASIC, 1, 1, basic_attrs, 0};
    eps[0] = (hal_zigbee_endpoint){2, 0, 0, 0, 2, clusters};
    device_config_str_t good;
    memset(&good, 0, sizeof good);
    memcpy(good.data, "a;b;", 4);
    good.size = 4;
    memcpy(nvm_config, &good, sizeof good);
    memcpy(&device_config_str, &good, sizeof good);
    nvm_writes = 0;
    gpio_writes = 0;
}

static int applied(uint8_t ep, uint16_t cid, uint16_t attr, uint8_t type) {
    return zcl_write_record_applied(ep, cid, attr, type, eps, 1) ? 1 : 0;
}

/* True SDK copy semantics (SDK V3.7.2.0 zcl_setAttrVal): the record value is
   memcpied into the registered destination before the hook runs. */
static void sdk_copy_u8(uint8_t *dst, uint8_t value) {
    *dst = value;
}

int main(void) {
    setup();
    /* Positive control: the recording doubles observe real dispatch. */
    zigbee_on_attr_change(2, ZCL_CLUSTER_WINDOW_COVERING, 0x0000);
    zigbee_on_attr_change(2, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, 0x0000);
    zigbee_on_attr_change(2, ZCL_CLUSTER_COVER_SWITCH_CONFIG, 0x0000);
    zigbee_on_attr_change(2, ZCL_CLUSTER_METERING, 0x0000);
    zigbee_on_attr_change(2, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0x0000);
    assert(switch_calls == 1 && cover_calls == 1 && cover_switch_calls == 1 &&
           metering_calls == 1 && elec_calls == 1);
    switch_calls = cover_calls = cover_switch_calls = metering_calls = elec_calls = 0;
    /* R1: valid writable record applies through the real dispatch path. */
    assert(applied(2, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
                   ZCL_DATA_TYPE_ENUM8) == 1);
    sdk_copy_u8(&cluster.physical_relay_mode,
                ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON);
    zigbee_on_attr_change(2, ZCL_CLUSTER_ON_OFF,
                          ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE);
    assert(cluster.physical_relay_mode == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON);
    assert(gpio_level[RELAY_PIN] == 1);
    assert(nvm_writes == 2);
    /* R2: read-only record dispatches nothing. */
    assert(applied(2, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF,
                   ZCL_DATA_TYPE_BOOLEAN) == 0);
    assert(relay.on == 0 && gpio_level[RELAY_PIN] == 1 && nvm_writes == 2);
    /* R3: registered-in-dispatch but absent-from-radio-table cluster. */
    assert(applied(2, ZCL_CLUSTER_WINDOW_COVERING, 0x0000,
                   ZCL_DATA_TYPE_UINT8) == 0);
    assert(applied(2, ZCL_CLUSTER_ON_OFF_SWITCH_CONFIG, 0x0000,
                   ZCL_DATA_TYPE_UINT8) == 0);
    /* R4: type mismatch and R5: absent endpoint dispatch nothing. */
    assert(applied(2, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
                   ZCL_DATA_TYPE_UINT16) == 0);
    assert(applied(9, ZCL_CLUSTER_ON_OFF, ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE,
                   ZCL_DATA_TYPE_ENUM8) == 0);
    assert(cluster.physical_relay_mode == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON);
    assert(nvm_writes == 2 && reboots == 0);
    /* R6: accepted-then-app-rejected config restores last-good, no reboot. */
    assert(applied(2, ZCL_CLUSTER_BASIC, ZCL_ATTR_BASIC_DEVICE_CONFIG,
                   ZCL_DATA_TYPE_LONG_CHAR_STR) == 1);
    device_config_str.size = 100;
    memset(device_config_str.data, 'Z', 100);
    zigbee_on_attr_change(2, ZCL_CLUSTER_BASIC, ZCL_ATTR_BASIC_DEVICE_CONFIG);
    assert(device_config_str.size == 4);
    assert(memcmp(device_config_str.data, "a;b;", 4) == 0);
    assert(config_item_writes == 0 && reboots == 0);
    /* R7: direct trampoline calls outside the instance table are refused. */
    nvm_writes = 0;
    relay_cluster_callback_attr_write_trampoline(10,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE);
    relay_cluster_callback_attr_write_trampoline(255,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE);
    relay_cluster_callback_attr_write_trampoline(3,
        ZCL_ATTR_ONOFF_PHYSICAL_RELAY_MODE);
    assert(nvm_writes == 0);
    assert(cluster.physical_relay_mode == ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON);
    assert(switch_calls == 0 && cover_calls == 0 && cover_switch_calls == 0 &&
           metering_calls == 0 && elec_calls == 0);
    return 0;
}
'''


def test_write_dispatch_predicate_mirrors_sdk_acceptance(tmp_path):
    binary = tmp_path / 'write-dispatch-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=PREDICATE_CODE, text=True, capture_output=True,
                   check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_mixed_write_dispatches_only_successful_records(tmp_path):
    binary = tmp_path / 'write-dispatch-mixed-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DVERSION_STR=1.2.5-bseedr7',
                    '-DDEFAULT_CONFIG=a;b;', '-ffunction-sections',
                    '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=DISPATCH_CODE, text=True, capture_output=True,
                   check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    hal = (ROOT / 'src/telink/hal/zigbee_zcl.c').read_text()
    assert 'zcl_write_record_applied' in hal
    assert 'ZCL_CMD_WRITE_UNDIVIDED' in hal
    for name in ('relay_cluster.c', 'cover_cluster.c',
                 'cover_switch_cluster.c', 'switch_cluster.c'):
        trampoline = (ROOT / 'src/zigbee' / name).read_text()
        assert 'endpoint >= 10' in trampoline, name
