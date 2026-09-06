#include "hlw8012.h"
#include "hal/timer.h"
#include "hal/printf_selector.h"
#include <string.h>

static void hlw8012_meter_get_data(void *ctx, energy_meter_data_t *data);
static void hlw8012_meter_reset_energy(void *ctx);
static void hlw8012_meter_tick(void *ctx);
static int hlw8012_meter_calibrate(void *ctx, energy_meter_channel_t channel,
                                   uint32_t reference);
static void hlw8012_meter_get_calibration(void *ctx,
                                          energy_meter_calibration_t *cal);
static void hlw8012_meter_set_calibration(void *ctx, uint32_t voltage_mult,
                                          uint32_t current_mult,
                                          uint32_t power_mult);
static int32_t hlw8012_meter_get_instant_power(void *ctx);
static void update_measurement_handler(void *arg);
static void cycle_sel_pin(hlw8012_t *dev);

static const energy_meter_ops_t hlw8012_energy_meter_ops = {
    .get_data          = hlw8012_meter_get_data,
    .reset_energy      = hlw8012_meter_reset_energy,
    .tick              = hlw8012_meter_tick,
    .calibrate         = hlw8012_meter_calibrate,
    .get_calibration   = hlw8012_meter_get_calibration,
    .set_calibration   = hlw8012_meter_set_calibration,
    .get_instant_power = hlw8012_meter_get_instant_power,
};

static uint32_t pulses_to_frequency(uint32_t pulse_count) {
    return pulse_count * (1000000u / HLW8012_SAMPLE_INTERVAL_MS);
}

int hlw8012_init(hlw8012_t *dev, hal_gpio_pin_t cf_pin,
                 hal_gpio_pin_t cf1_pin, hal_gpio_pin_t sel_pin) {
    if (!dev || cf_pin == HAL_INVALID_PIN || cf1_pin == HAL_INVALID_PIN ||
        sel_pin == HAL_INVALID_PIN) {
        return -1;
    }

    memset(dev, 0, sizeof(*dev));
    dev->cf_pin      = cf_pin;
    dev->cf1_pin     = cf1_pin;
    dev->sel_pin     = sel_pin;
    dev->cf_counter  = HAL_GPIO_COUNTER_INVALID;
    dev->cf1_counter = HAL_GPIO_COUNTER_INVALID;

    dev->cal.voltage_multiplier = HLW8012_VOLTAGE_MULTIPLIER;
    dev->cal.current_multiplier = HLW8012_CURRENT_MULTIPLIER;
    dev->cal.power_multiplier   = HLW8012_POWER_MULTIPLIER;

    dev->cf_counter =
        hal_gpio_counter_init(cf_pin, HAL_GPIO_COUNTER_RISING,
                              HAL_GPIO_PULL_NONE);
    if (dev->cf_counter == HAL_GPIO_COUNTER_INVALID) {
        printf("HLW8012: no pulse counter available for CF\r\n");
        return -1;
    }

    dev->cf1_counter =
        hal_gpio_counter_init(cf1_pin, HAL_GPIO_COUNTER_RISING,
                              HAL_GPIO_PULL_NONE);
    if (dev->cf1_counter == HAL_GPIO_COUNTER_INVALID) {
        printf("HLW8012: no pulse counter available for CF1\r\n");
        hal_gpio_counter_deinit(dev->cf_counter);
        dev->cf_counter = HAL_GPIO_COUNTER_INVALID;
        return -1;
    }

    /* V8 relay work established a first-driven-level GPIO contract. Reuse it
     * for SEL so meter startup does not briefly select the wrong channel. */
    hal_gpio_init_output(sel_pin, HAL_GPIO_PULL_NONE, 1);

    dev->data.sel_state        = 1;
    dev->data.last_sample_time = hal_millis();
    dev->initialized           = 1;
    dev->update_task.handler   = update_measurement_handler;
    dev->update_task.arg       = dev;
    hal_tasks_init(&dev->update_task);

    energy_meter_init(&dev->meter, &hlw8012_energy_meter_ops, dev,
                      ENERGY_METER_HLW8012);

    printf("HLW8012: Initialized on CF=%04x CF1=%04x SEL=%04x\r\n", cf_pin,
           cf1_pin, sel_pin);
    hal_tasks_schedule(&dev->update_task, HLW8012_SAMPLE_INTERVAL_MS);
    return 0;
}

void hlw8012_set_sel_inverted(hlw8012_t *dev, uint8_t inverted) {
    if (!dev)
        return;

    dev->sel_inverted = inverted ? 1 : 0;
    dev->meter.type   = dev->sel_inverted ? ENERGY_METER_BL0937
                                         : ENERGY_METER_HLW8012;
    printf("HLW8012: SEL polarity %s\r\n",
           dev->sel_inverted ? "inverted (BL0937)" : "normal (HLW8012)");
}

void hlw8012_set_calibration(hlw8012_t *dev, uint32_t voltage_mult,
                             uint32_t current_mult, uint32_t power_mult) {
    if (!dev)
        return;

    if (voltage_mult)
        dev->cal.voltage_multiplier = voltage_mult;
    if (current_mult)
        dev->cal.current_multiplier = current_mult;
    if (power_mult)
        dev->cal.power_multiplier = power_mult;

    printf("HLW8012: calibration V=%u A=%u W=%u\r\n",
           dev->cal.voltage_multiplier, dev->cal.current_multiplier,
           dev->cal.power_multiplier);
}

int hlw8012_calibrate(hlw8012_t *dev, uint8_t channel, uint32_t reference) {
    if (!dev || reference == 0)
        return -1;

    uint32_t  pulses;
    uint32_t *target;
    switch (channel) {
    case ENERGY_METER_CHANNEL_VOLTAGE:
        pulses = dev->data.cal_pulses_voltage;
        target = &dev->cal.voltage_multiplier;
        break;
    case ENERGY_METER_CHANNEL_CURRENT:
        pulses = dev->data.cal_pulses_current;
        target = &dev->cal.current_multiplier;
        break;
    case ENERGY_METER_CHANNEL_POWER:
        pulses = dev->data.cal_pulses_power;
        target = &dev->cal.power_multiplier;
        break;
    default:
        return -1;
    }

    if (pulses == 0)
        return -1;

    *target = ((uint32_t)reference * (uint32_t)HLW8012_FIXED_POINT_SCALE) /
              pulses;
    printf("HLW8012: calibrated ch %u to ref %u (%u pulses) => mult %u\r\n",
           channel, reference, pulses, *target);
    return 0;
}

static int hlw8012_meter_calibrate(void *ctx, energy_meter_channel_t channel,
                                   uint32_t reference) {
    return hlw8012_calibrate((hlw8012_t *)ctx, (uint8_t)channel, reference);
}

static void hlw8012_meter_get_calibration(void *ctx,
                                          energy_meter_calibration_t *cal) {
    hlw8012_t *dev = (hlw8012_t *)ctx;

    if (!dev || !cal)
        return;

    cal->voltage_multiplier = dev->cal.voltage_multiplier;
    cal->current_multiplier = dev->cal.current_multiplier;
    cal->power_multiplier   = dev->cal.power_multiplier;
}

static void hlw8012_meter_set_calibration(void *ctx, uint32_t voltage_mult,
                                          uint32_t current_mult,
                                          uint32_t power_mult) {
    hlw8012_set_calibration((hlw8012_t *)ctx, voltage_mult, current_mult,
                            power_mult);
}

static void update_measurement_handler(void *arg) {
    hlw8012_t *dev = (hlw8012_t *)arg;

    if (!dev || !dev->initialized)
        return;

    uint32_t now        = hal_millis();
    uint32_t cf_pulses  = hal_gpio_counter_read_and_reset(dev->cf_counter);
    uint32_t cf1_pulses = hal_gpio_counter_read_and_reset(dev->cf1_counter);

    if (cf_pulses > HLW8012_MAX_SANE_PULSES)
        cf_pulses = 0;
    if (cf1_pulses > HLW8012_MAX_SANE_PULSES)
        cf1_pulses = 0;

    dev->data.freq_cf  = pulses_to_frequency(cf_pulses);
    dev->data.freq_cf1 = pulses_to_frequency(cf1_pulses);
    dev->data.power    =
        (int16_t)(((uint32_t)cf_pulses * dev->cal.power_multiplier +
                   HLW8012_FIXED_POINT_SCALE / 2) /
                  HLW8012_FIXED_POINT_SCALE);
    dev->data.cal_pulses_power = cf_pulses;

    if (dev->cycle_count != 0) {
        uint8_t reading_voltage =
            dev->sel_inverted ? !dev->data.sel_state : dev->data.sel_state;
        if (reading_voltage) {
            dev->data.voltage =
                (uint16_t)(((uint32_t)cf1_pulses *
                            dev->cal.voltage_multiplier) /
                           HLW8012_FIXED_POINT_SCALE);
            dev->data.cal_pulses_voltage = cf1_pulses;
        } else {
            dev->data.current =
                (uint16_t)(((uint32_t)cf1_pulses *
                            dev->cal.current_multiplier) /
                           HLW8012_FIXED_POINT_SCALE);
            dev->data.cal_pulses_current = cf1_pulses;
        }
    }

    /* Proven b28wrpvx filter: do not let the calibrated idle pulse floor
     * accumulate phantom energy or feed overload protection. A real load exits
     * suppression immediately; voltage and raw pulse diagnostics remain live. */
    if (dev->data.power <= HLW8012_NO_LOAD_POWER_W &&
        dev->data.current <= HLW8012_NO_LOAD_CURRENT_MA) {
        if (dev->data.no_load_samples < HLW8012_NO_LOAD_CONFIRM_SAMPLES)
            dev->data.no_load_samples++;
        if (dev->data.no_load_samples == HLW8012_NO_LOAD_CONFIRM_SAMPLES) {
            dev->data.no_load_suppressed = 1;
            dev->data.current            = 0;
            dev->data.power = 0;
        }
    } else {
        dev->data.no_load_samples    = 0;
        dev->data.no_load_suppressed = 0;
    }

    if (!dev->data.no_load_suppressed) {
        dev->data.energy_acc +=
            (uint32_t)cf_pulses * dev->cal.power_multiplier;
        while (dev->data.energy_acc >= HLW8012_ENERGY_WH_SUBUNIT) {
            dev->data.energy_acc -= HLW8012_ENERGY_WH_SUBUNIT;
            dev->data.energy++;
        }
    }

    dev->data.valid            = 1;
    dev->data.last_sample_time = now;
    dev->cycle_count++;
    cycle_sel_pin(dev);
    hal_tasks_schedule(&dev->update_task, HLW8012_SAMPLE_INTERVAL_MS);
}

static void cycle_sel_pin(hlw8012_t *dev) {
    if (dev->cycle_count == HLW8012_SEL_TOGGLE_CYCLE_INTERVAL) {
        if (dev->data.sel_state) {
            hal_gpio_clear(dev->sel_pin);
            dev->data.sel_state = 0;
        } else {
            hal_gpio_set(dev->sel_pin);
            dev->data.sel_state = 1;
        }
        dev->cycle_count = 0;
    }
}

hlw8012_data_t *hlw8012_get_data(hlw8012_t *dev) {
    return dev ? &dev->data : NULL;
}

void hlw8012_reset_energy(hlw8012_t *dev) {
    if (!dev)
        return;

    dev->data.energy     = 0;
    dev->data.energy_acc = 0;
    hal_gpio_counter_read_and_reset(dev->cf_counter);
    hal_gpio_counter_read_and_reset(dev->cf1_counter);
}

static int32_t hlw8012_meter_get_instant_power(void *ctx) {
    hlw8012_t *dev = (hlw8012_t *)ctx;

    if (!dev || !dev->initialized || !dev->data.valid)
        return dev ? dev->data.power : 0;

    uint32_t elapsed = hal_millis() - dev->data.last_sample_time;
    if (elapsed < 1000u)
        return dev->data.power;

    uint32_t partial     = hal_gpio_counter_read(dev->cf_counter);
    uint32_t pulses_full =
        (partial * HLW8012_SAMPLE_INTERVAL_MS) / elapsed;
    if (pulses_full > HLW8012_MAX_SANE_PULSES)
        pulses_full = HLW8012_MAX_SANE_PULSES;

    int32_t power =
        (int32_t)((pulses_full * dev->cal.power_multiplier +
                   HLW8012_FIXED_POINT_SCALE / 2) /
                  HLW8012_FIXED_POINT_SCALE);
    if (dev->data.no_load_suppressed && power <= HLW8012_NO_LOAD_POWER_W)
        return 0;

    return power;
}

static void hlw8012_meter_get_data(void *ctx, energy_meter_data_t *data) {
    hlw8012_t *dev = (hlw8012_t *)ctx;

    if (!dev || !data)
        return;

    data->voltage   = dev->data.voltage;
    data->current   = dev->data.current;
    data->power     = dev->data.power;
    data->energy    = dev->data.energy;
    data->freq_cf   = dev->data.freq_cf;
    data->freq_cf1  = dev->data.freq_cf1;
    data->sel_state = dev->data.sel_state;
    data->valid     = dev->data.valid;
}

static void hlw8012_meter_reset_energy(void *ctx) {
    hlw8012_reset_energy((hlw8012_t *)ctx);
}

static void hlw8012_meter_tick(void *ctx) {
    hlw8012_tick((hlw8012_t *)ctx);
}

energy_meter_t *hlw8012_as_energy_meter(hlw8012_t *dev) {
    if (!dev || !dev->initialized)
        return NULL;

    return &dev->meter;
}

void hlw8012_tick(hlw8012_t *dev) {
    (void)dev;
}
