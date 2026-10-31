"""PM calibration writes cannot disable overload protection via the meter."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include <assert.h>
#include <string.h>
#include "base_components/energy_measurement/hlw8012.c"

uint32_t hal_millis(void) { return 0; }
hal_gpio_counter_t hal_gpio_counter_init(hal_gpio_pin_t pin,
                                         hal_gpio_counter_edge_t edge,
                                         hal_gpio_pull_t pull) {
    (void)pin; (void)edge; (void)pull; return 0;
}
void hal_gpio_counter_deinit(hal_gpio_counter_t counter) { (void)counter; }
uint32_t hal_gpio_counter_read(hal_gpio_counter_t counter) { (void)counter; return 0; }
void hal_gpio_counter_reset(hal_gpio_counter_t counter) { (void)counter; }
void hal_gpio_counter_start(hal_gpio_counter_t counter) { (void)counter; }
void hal_gpio_counter_stop(hal_gpio_counter_t counter) { (void)counter; }
void hal_gpio_init(hal_gpio_pin_t pin, uint8_t in, hal_gpio_pull_t pull) {
    (void)pin; (void)in; (void)pull;
}
void hal_gpio_set(hal_gpio_pin_t pin) { (void)pin; }
void hal_gpio_clear(hal_gpio_pin_t pin) { (void)pin; }
void hal_tasks_init(hal_task_t *task) { (void)task; }
void hal_tasks_schedule(hal_task_t *task, uint32_t ms) { (void)task; (void)ms; }

static hlw8012_t dev;

static void reset_defaults(void) {
    memset(&dev, 0, sizeof dev);
    dev.cal.voltage_multiplier = HLW8012_VOLTAGE_MULTIPLIER;
    dev.cal.current_multiplier = HLW8012_CURRENT_MULTIPLIER;
    dev.cal.power_multiplier   = HLW8012_POWER_MULTIPLIER;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const int pm = !strcmp(argv[1], "pm");
    reset_defaults();
    /* Zero still means "keep", on every board. */
    hlw8012_set_calibration(&dev, 0, 0, 0);
    assert(dev.cal.power_multiplier == HLW8012_POWER_MULTIPLIER);
    /* Plausible field calibration is applied unchanged. */
    hlw8012_set_calibration(&dev, HLW8012_VOLTAGE_MULTIPLIER + 1000,
                            HLW8012_CURRENT_MULTIPLIER - 1000,
                            HLW8012_POWER_MULTIPLIER * 2);
    assert(dev.cal.voltage_multiplier == HLW8012_VOLTAGE_MULTIPLIER + 1000);
    assert(dev.cal.current_multiplier == HLW8012_CURRENT_MULTIPLIER - 1000);
    assert(dev.cal.power_multiplier == HLW8012_POWER_MULTIPLIER * 2);
    /* "W1A1" would read every overload as ~0 W / ~0 mA. */
    reset_defaults();
    hlw8012_set_calibration(&dev, 0, 1, 1);
    if (pm) {
        assert(dev.cal.current_multiplier == HLW8012_CURRENT_MULTIPLIER);
        assert(dev.cal.power_multiplier == HLW8012_POWER_MULTIPLIER);
    } else {
        /* Generic boards keep their unrestricted config-driven behavior. */
        assert(dev.cal.current_multiplier == 1 && dev.cal.power_multiplier == 1);
    }
    reset_defaults();
    hlw8012_set_calibration(&dev, 0xFFFFFFFFu, 0, HLW8012_POWER_MULTIPLIER * 5);
    if (pm) {
        assert(dev.cal.voltage_multiplier == HLW8012_VOLTAGE_MULTIPLIER);
        assert(dev.cal.power_multiplier == HLW8012_POWER_MULTIPLIER);
    }
    /* Reference calibration that implies a 100x factor is refused. */
    reset_defaults();
    dev.data.cal_pulses_power = 100;
    uint32_t absurd = HLW8012_POWER_MULTIPLIER;  /* ~100x expected reference */
    int rc = hlw8012_calibrate(&dev, ENERGY_METER_CHANNEL_POWER, absurd);
    if (pm) {
        assert(rc == -1 && dev.cal.power_multiplier == HLW8012_POWER_MULTIPLIER);
    } else {
        assert(rc == 0);
    }
    /* A reference consistent with the verified factor is accepted. */
    reset_defaults();
    dev.data.cal_pulses_power = 1000;
    uint32_t reference = (uint32_t)((1000ull * HLW8012_POWER_MULTIPLIER) /
                                    HLW8012_FIXED_POINT_SCALE);
    assert(hlw8012_calibrate(&dev, ENERGY_METER_CHANNEL_POWER, reference) == 0);
    return 0;
}
'''


@pytest.mark.parametrize('board', ['pm', 'generic'])
def test_pm_calibration_band_and_generic_compatibility(tmp_path, board):
    flags = ['-DBSEED_PM_B28WRPVX=1', '-DHLW8012_VOLTAGE_MULTIPLIER=161460',
             '-DHLW8012_CURRENT_MULTIPLIER=144679',
             '-DHLW8012_POWER_MULTIPLIER=16989'] if board == 'pm' else []
    binary = tmp_path / f'calibration-{board}'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', *flags,
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=CODE, text=True, check=True)
    subprocess.run([str(binary), board], check=True, timeout=5)
