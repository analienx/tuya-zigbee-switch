"""LED brightness (Basic 0xFF03) through the real basic cluster and LED code.

A written brightness takes effect only after it is stored, so a failed NV
write never looks applied until the next reboot; the attribute then reads
back the brightness still in effect. A reboot restores the stored value.
Builds without LED_DIMMING register no such attribute."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include <assert.h>
#include <string.h>
#include "device_config/config_nv.c"
#include "base_components/led.c"
#include "zigbee/basic_cluster.c"

uint8_t g_multi_press_reset_count;
static uint8_t stored_brightness;
static bool brightness_present, fail_writes;
static unsigned brightness_writes, refreshes;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (item == NV_ITEM_LED_BRIGHTNESS && brightness_present && size == 1) {
        *data = stored_brightness;
        return HAL_NVM_SUCCESS;
    }
    return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    if (item != NV_ITEM_LED_BRIGHTNESS)
        return HAL_NVM_SUCCESS;
    brightness_writes++;
    if (fail_writes)
        return HAL_NVM_NOT_FOUND;
    assert(size == 1);
    stored_brightness = *data;
    brightness_present = true;
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}
void hal_tasks_init(hal_task_t *t) { (void)t; }
void hal_tasks_schedule(hal_task_t *t, uint32_t ms) { (void)t; (void)ms; }
void hal_tasks_unschedule(hal_task_t *t) { (void)t; }
void schedule_reboot(uint16_t delay_ms) { (void)delay_ms; }
network_indicator_t network_indicator;
void network_indicator_from_manual_state(network_indicator_t *indicator) { (void)indicator; }
void peripherals_refresh_leds(void) { refreshes++; }
void device_params_set_multi_press_reset_count(uint8_t value) { (void)value; }

static void write_brightness(uint8_t value) {  /* what the ZCL write does */
    led_brightness_attr = value;
    basic_cluster_callback_attr_write_trampoline(ZCL_ATTR_BASIC_LED_BRIGHTNESS);
}

int main(void) {
    /* Fresh device: build default, nothing stored. */
    basic_cluster_load_attrs_from_nv();
    assert(led_get_brightness() == LED_DEFAULT_BRIGHTNESS);
    assert(led_brightness_attr == LED_DEFAULT_BRIGHTNESS);

    /* Write: stored first, then applied; readback matches. */
    write_brightness(30);
    assert(brightness_writes == 1 && stored_brightness == 30);
    assert(led_get_brightness() == 30 && led_brightness_attr == 30 && refreshes >= 2);

    /* Failed NV write: nothing changes, readback is the value in effect. */
    fail_writes = true;
    write_brightness(80);
    assert(brightness_writes == 2);
    assert(led_get_brightness() == 30 && led_brightness_attr == 30 && stored_brightness == 30);
    fail_writes = false;

    /* Out-of-range writes are clamped and stored clamped. */
    write_brightness(0);
    assert(stored_brightness == LED_MIN_BRIGHTNESS && led_brightness_attr == LED_MIN_BRIGHTNESS);
    write_brightness(250);
    assert(stored_brightness == LED_MAX_BRIGHTNESS && led_get_brightness() == LED_MAX_BRIGHTNESS);

    /* Rewriting the value in effect needs no NV write. */
    unsigned writes = brightness_writes;
    write_brightness(LED_MAX_BRIGHTNESS);
    assert(brightness_writes == writes);

    /* Reboot: the stored value is restored, not the build default. */
    write_brightness(40);
    led_set_brightness(LED_DEFAULT_BRIGHTNESS);
    led_brightness_attr = LED_DEFAULT_BRIGHTNESS;
    basic_cluster_load_attrs_from_nv();
    assert(led_get_brightness() == 40 && led_brightness_attr == 40);

    /* A corrupt stored value is ignored. */
    stored_brightness = 0;
    led_set_brightness(LED_DEFAULT_BRIGHTNESS);
    basic_cluster_load_attrs_from_nv();
    assert(led_get_brightness() == LED_DEFAULT_BRIGHTNESS);
    return 0;
}
'''


def test_brightness_is_applied_only_once_stored_and_survives_reboot(tmp_path):
    binary = tmp_path / 'led-brightness-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DVERSION_STR=1.2.5-bseedr24',
                    '-DLED_DIMMING=1', '-DLED_DEFAULT_BRIGHTNESS=50',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), str(ROOT / 'src/stub/hal/gpio.c'),
                    '-x', 'c', '-', '-o', str(binary)],
                   input=CODE, text=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_builds_without_dimming_register_no_brightness_attribute():
    basic = (ROOT / 'src/zigbee/basic_cluster.c').read_text()
    setup = basic.split('SETUP_ATTR(attr_count, ZCL_ATTR_BASIC_LED_BRIGHTNESS', 1)[0]
    assert setup.rstrip().endswith('#ifdef LED_DIMMING')
    gpio = (ROOT / 'src/telink/hal/gpio.c').read_text()
    assert '#ifdef LED_DIMMING\n#if PM_ENABLE\n#error' in gpio
    makefile = (ROOT / 'src/telink/Makefile').read_text()
    assert '-DLED_DIMMING=1' in makefile
    gen = (ROOT / 'helper_scripts/make_z2m_custom_converters.py').read_text()
    assert '"led_dimming": db_key in {"OUTLET_BSEED_PM_TS011F", "OUTLET_BSEED_TS011F"}' in gen
    for path in ('zigbee2mqtt/converters/switch_custom.js',
                 'zigbee2mqtt/converters_v1/switch_custom.js'):
        assert (ROOT / path).read_text().count('romasku.ledBrightness("led_brightness"') == 2
