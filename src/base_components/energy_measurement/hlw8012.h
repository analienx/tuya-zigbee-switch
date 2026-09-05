#ifndef _HLW8012_H_
#define _HLW8012_H_

#include <stdint.h>
#include "hal/gpio.h"
#include "base_components/energy_meter.h"
#include "hal/tasks.h"

#define HLW8012_FIXED_POINT_SCALE            65536
#ifndef HLW8012_POWER_MULTIPLIER
#define HLW8012_POWER_MULTIPLIER             13939
#endif
#ifndef HLW8012_VOLTAGE_MULTIPLIER
#define HLW8012_VOLTAGE_MULTIPLIER           154672
#endif
#ifndef HLW8012_CURRENT_MULTIPLIER
#define HLW8012_CURRENT_MULTIPLIER           118646
#endif
#define HLW8012_SEL_TOGGLE_CYCLE_INTERVAL    5
#define HLW8012_PULSE_TIMEOUT_MS             20000
#define HLW8012_SAMPLE_INTERVAL_MS           5000
#define HLW8012_MAX_SANE_PULSES              30000

/* Hardware-proven BSEED b28wrpvx no-load envelope. Three consecutive low
 * samples are required before residual BL0937 pulses are suppressed. */
#define HLW8012_NO_LOAD_POWER_W              2
#define HLW8012_NO_LOAD_CURRENT_MA           50
#define HLW8012_NO_LOAD_CONFIRM_SAMPLES      3

#define HLW8012_ENERGY_WH_SUBUNIT            \
        (HLW8012_FIXED_POINT_SCALE * 3600u / \
         (HLW8012_SAMPLE_INTERVAL_MS / 1000u))

typedef struct {
    uint32_t cf_pulse_count;
    uint32_t cf_last_pulse_time;
    uint32_t cf_total_pulse_count;
    uint32_t cf1_pulse_count;
    uint32_t cf1_last_pulse_time;
    uint32_t cf1_total_pulse_count;
    uint32_t last_sample_time;
    uint32_t cf_tick_pulse_count;
    uint32_t cf1_tick_pulse_count;
    uint8_t  cf_last_gpio_state;
    uint8_t  cf1_last_gpio_state;
    uint16_t voltage;
    uint16_t current;
    int16_t  power;
    uint32_t energy;
    uint32_t energy_acc;
    uint8_t  no_load_samples;
    uint8_t  no_load_suppressed;
    uint8_t  sel_state;
    uint8_t  valid;
    uint32_t freq_cf;
    uint32_t freq_cf1;
    uint32_t cal_pulses_voltage;
    uint32_t cal_pulses_current;
    uint32_t cal_pulses_power;
} hlw8012_data_t;

typedef struct {
    uint32_t voltage_multiplier;
    uint32_t current_multiplier;
    uint32_t power_multiplier;
} hlw8012_calibration_t;

typedef struct {
    hal_gpio_pin_t        cf_pin;
    hal_gpio_pin_t        cf1_pin;
    hal_gpio_pin_t        sel_pin;
    hal_gpio_counter_t    cf_counter;
    hal_gpio_counter_t    cf1_counter;
    hlw8012_data_t        data;
    hlw8012_calibration_t cal;
    hal_task_t            update_task;
    uint8_t               cycle_count;
    uint8_t               initialized;
    uint8_t               sel_inverted;
    energy_meter_t        meter;
} hlw8012_t;

int hlw8012_init(hlw8012_t *dev, hal_gpio_pin_t cf_pin,
                 hal_gpio_pin_t cf1_pin, hal_gpio_pin_t sel_pin);
void hlw8012_set_sel_inverted(hlw8012_t *dev, uint8_t inverted);
void hlw8012_set_calibration(hlw8012_t *dev, uint32_t voltage_mult,
                             uint32_t current_mult, uint32_t power_mult);
int hlw8012_calibrate(hlw8012_t *dev, uint8_t channel, uint32_t reference);
hlw8012_data_t *hlw8012_get_data(hlw8012_t *dev);
void hlw8012_reset_energy(hlw8012_t *dev);
energy_meter_t *hlw8012_as_energy_meter(hlw8012_t *dev);
void hlw8012_tick(hlw8012_t *dev);

#endif /* _HLW8012_H_ */
