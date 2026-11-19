"""Run the real Telink LED PWM routing against register doubles on CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "hal/gpio.h"
#define CLOCK_SYS_CLOCK_HZ 24000000u
typedef enum { PWM0_ID, PWM1_ID, PWM2_ID, PWM3_ID, PWM4_ID, PWM5_ID } pwm_id;
typedef enum { AS_GPIO, AS_PWM0, AS_PWM1, AS_PWM2, AS_PWM3, AS_PWM4, AS_PWM5 } GPIO_FuncTypeDef;
typedef uint16_t GPIO_PinTypeDef;
#define GPIO_PA2 0x004
#define GPIO_PA3 0x008
#define GPIO_PA4 0x010
#define GPIO_PB0 0x101
#define GPIO_PB1 0x102
#define GPIO_PB2 0x104
#define GPIO_PB4 0x110
#define GPIO_PB5 0x120
#define GPIO_PC1 0x202
#define GPIO_PC2 0x204
#define GPIO_PC3 0x208
#define GPIO_PC4 0x210
#define GPIO_PD2 0x304
#define GPIO_PD5 0x320
#define PWM_NORMAL_MODE 0
static unsigned programs[6], starts[6], stops[6], modes[6], phases[6], clocks;
static uint16_t compare[6];
static uint8_t levels[0x400];
static GPIO_FuncTypeDef functions[0x400];
typedef struct { uint8_t value; } gpio_config_t;
static gpio_config_t config;
static gpio_config_t *find_gpio_config(hal_gpio_pin_t pin) { (void)pin; return &config; }
void hal_gpio_set(hal_gpio_pin_t pin) { levels[pin] = 1; }
void hal_gpio_clear(hal_gpio_pin_t pin) { levels[pin] = 0; }
static void gpio_set_func(GPIO_PinTypeDef pin, GPIO_FuncTypeDef func) { functions[pin] = func; }
static void pwm_set_clk(unsigned sys, unsigned clk) { assert(sys == 24000000u && clk == 1000000u); clocks++; }
static void pwm_set_mode(pwm_id id, unsigned mode) { assert(mode == 0); modes[id]++; }
static void pwm_set_phase(pwm_id id, unsigned phase) { assert(phase == 0); phases[id]++; }
static void pwm_set_cycle_and_duty(pwm_id id, uint16_t cycle, uint16_t high) {
    assert(cycle == 1000); compare[id] = high; programs[id]++;
}
static void pwm_start(pwm_id id) { starts[id]++; }
static void pwm_stop(pwm_id id) { stops[id]++; }
'''

CASES = r'''
int main(void) {
    /* Both BSEED boards use separate channels: PM C3/B4, non-PM C2/D2. */
    const uint16_t pins[] = {GPIO_PC3, GPIO_PB4, GPIO_PC2, GPIO_PD2};
    const pwm_id ids[] = {PWM1_ID, PWM4_ID, PWM0_ID, PWM3_ID};
    for (unsigned i = 0; i < 4; i++) {
        hal_gpio_write_duty(pins[i], 1, 50);
        assert(compare[ids[i]] == 500 && programs[ids[i]] == 1);
        assert(functions[pins[i]] != AS_GPIO && starts[ids[i]] == 1);
        hal_gpio_write_duty(pins[i], 1, 50);
        assert(programs[ids[i]] == 1 && starts[ids[i]] == 1);
        hal_gpio_write_duty(pins[i], 0, 100);
        assert(functions[pins[i]] == AS_GPIO && levels[pins[i]] == 0);
        assert(stops[ids[i]] == 1);
        hal_gpio_write_duty(pins[i], 1, 50);
        assert(programs[ids[i]] == 1 && starts[ids[i]] == 2);
        hal_gpio_write_duty(pins[i], 0, 20);  /* active-low: high 80% */
        assert(compare[ids[i]] == 800 && programs[ids[i]] == 2);
        assert(modes[ids[i]] == 1 && phases[ids[i]] == 1);
        hal_gpio_write_duty(pins[i], 1, 100);
        assert(functions[pins[i]] == AS_GPIO && levels[pins[i]] == 1);
    }
    assert(clocks == 1);
    /* B1 aliases B4's PWM4. It must remain GPIO and leave its owner alone. */
    unsigned before = programs[PWM4_ID];
    hal_gpio_write_duty(GPIO_PB1, 1, 25);
    hal_gpio_write_duty(GPIO_PB1, 0, 25);
    assert(functions[GPIO_PB1] == AS_GPIO && levels[GPIO_PB1] == 0);
    assert(programs[PWM4_ID] == before && hal_pwm_owner[PWM4_ID] == GPIO_PB4);
    /* Non-PWM pin falls back to GPIO too. */
    hal_gpio_write_duty(1, 1, 25);
    assert(levels[1] == 1);
    return 0;
}
'''


def test_pwm_routing_caching_and_conflict_fallback(tmp_path):
    source = (ROOT / 'src/telink/hal/gpio.c').read_text()
    pwm = source.split('#ifdef LED_DIMMING\n', 1)[1].split(
        '\n#else\nvoid hal_gpio_write_duty', 1)[0]
    binary = tmp_path / 'led-pwm'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-DHAL_STUB',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=PRELUDE + pwm + CASES, text=True, check=True)
    result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
    assert result.stdout.count('shares PWM4, not dimmed') == 1
