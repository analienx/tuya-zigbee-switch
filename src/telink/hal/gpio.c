#include "hal/gpio.h"
#include "atoi.h"
#include "hal/tasks.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)

#include <stdint.h>
#include <string.h>
// hal_gpio_pin_t directly stores GPIO_PinTypeDef values

// GPIO config tracking for deep retention re-init
// After deep retention wake, all GPIO SFRs are lost and must be replayed.
// 32 should cover all GPIOs, as we have 4 ports with 8 pins each.
#define MAX_GPIO_CONFIGS    32


typedef struct {
    hal_gpio_pin_t  pin;
    uint8_t         is_input;
    hal_gpio_pull_t pull;
    uint8_t         value;
} gpio_config_t;
static gpio_config_t gpio_configs[MAX_GPIO_CONFIGS];
static uint8_t       gpio_config_cnt = 0;

static gpio_config_t *find_gpio_config(hal_gpio_pin_t pin) {
    for (uint8_t i = 0; i < gpio_config_cnt; i++) {
        if (gpio_configs[i].pin == pin) {
            return &gpio_configs[i];
        }
    }
    return NULL;
}

// Convert HAL pull type to Telink pull type
static GPIO_PullTypeDef hal_to_telink_pull(hal_gpio_pull_t pull) {
    switch (pull) {
    case HAL_GPIO_PULL_NONE:
        return PM_PIN_UP_DOWN_FLOAT;

    case HAL_GPIO_PULL_UP:
        return PM_PIN_PULLUP_10K;

    case HAL_GPIO_PULL_UP_1M:
        return PM_PIN_PULLUP_1M;

    case HAL_GPIO_PULL_DOWN:
        return PM_PIN_PULLDOWN_100K;

    default:
        return PM_PIN_UP_DOWN_FLOAT;
    }
}

static void gpio_init_hw(hal_gpio_pin_t gpio_pin, uint8_t is_input,
                         hal_gpio_pull_t pull) {
    GPIO_PinTypeDef telink_pin = (GPIO_PinTypeDef)gpio_pin;

    if (is_input) {
        gpio_set_input_en(telink_pin, 1);
        gpio_set_output_en(telink_pin, 0);
    } else {
        gpio_set_output_en(telink_pin, 1);
        gpio_set_input_en(telink_pin, 0);
    }

    // Set pull resistor
    gpio_setup_up_down_resistor(telink_pin, hal_to_telink_pull(pull));
}

void hal_gpio_init(hal_gpio_pin_t gpio_pin, uint8_t is_input,
                   hal_gpio_pull_t pull) {
    gpio_init_hw(gpio_pin, is_input, pull);

    gpio_config_t *config = find_gpio_config(gpio_pin);
    if (config == NULL) {
        if (gpio_config_cnt >= MAX_GPIO_CONFIGS) {
            return;
        }
        config = &gpio_configs[gpio_config_cnt++];
    }

    // Track config for deep retention re-init
    config->pin      = gpio_pin;
    config->is_input = is_input;
    config->value    = 0;
    config->pull     = pull;
}

void hal_gpio_init_output(hal_gpio_pin_t gpio_pin, hal_gpio_pull_t pull,
                          uint8_t initial_value) {
    // Preload the output data register BEFORE enabling the output driver so
    // the first driven level is already `initial_value` (no LOW-then-HIGH
    // boot glitch). Mirrors the deep-retention restore ordering above.
    gpio_write((GPIO_PinTypeDef)gpio_pin, initial_value);
    gpio_init_hw(gpio_pin, 0, pull);

    gpio_config_t *config = find_gpio_config(gpio_pin);
    if (config == NULL) {
        if (gpio_config_cnt >= MAX_GPIO_CONFIGS) {
            return;
        }
        config = &gpio_configs[gpio_config_cnt++];
    }

    config->pin      = gpio_pin;
    config->is_input = 0;
    config->value    = initial_value ? 1 : 0;
    config->pull     = pull;
}

void telink_gpio_reinit_after_deep_retention(void) {
    for (uint8_t i = 0; i < gpio_config_cnt; i++) {
        if (!gpio_configs[i].is_input) {
            // Restore output value before reconfiguring pin,
            // to avoid driving it to the 0 level unconditionally
            // briefly during re-init
            gpio_write((GPIO_PinTypeDef)gpio_configs[i].pin, gpio_configs[i].value);
        }
        gpio_init_hw(gpio_configs[i].pin, gpio_configs[i].is_input,
                     gpio_configs[i].pull);
    }
}

void telink_gpio_to_pull_for_deep_retention() {
    // Uses pull-up or downs to emulate output levels during deep retention,
    // as telink doesn't support true GPIO output retention.
    for (uint8_t i = 0; i < gpio_config_cnt; i++) {
        if (gpio_configs[i].is_input) {
            continue;
        }
        GPIO_PinTypeDef  telink_pin = (GPIO_PinTypeDef)gpio_configs[i].pin;
        GPIO_PullTypeDef pull       =
            gpio_configs[i].value ? PM_PIN_PULLUP_1M : PM_PIN_PULLDOWN_100K;
        gpio_set_input_en(telink_pin, 1);
        gpio_set_output_en(telink_pin, 0);
        gpio_setup_up_down_resistor(telink_pin, pull);
    }
}

void hal_gpio_set(hal_gpio_pin_t gpio_pin) {
    gpio_write((GPIO_PinTypeDef)gpio_pin, 1);
    gpio_config_t *config = find_gpio_config(gpio_pin);
    if (config) {
        config->value = 1;
    }
}

void hal_gpio_clear(hal_gpio_pin_t gpio_pin) {
    gpio_write((GPIO_PinTypeDef)gpio_pin, 0);
    gpio_config_t *config = find_gpio_config(gpio_pin);
    if (config) {
        config->value = 0;
    }
}

#ifdef LED_DIMMING
#if PM_ENABLE
#error "LED_DIMMING needs a non-sleeping build: PWM stops in sleep"
#endif

/* LED dimming on the TLSR8258 hardware PWM channels.
 *
 * Pin-to-channel map from the SDK's gpio_set_func() (chip_8258/gpio.c), only
 * the non-inverted outputs. Two pins sharing a channel cannot be dimmed
 * independently: the first pin to use a channel owns it, any other pin on
 * that channel stays on plain full-level GPIO. The PWM block stops in sleep,
 * so a PM_ENABLE build with LED_DIMMING is a compile error above. */
#define HAL_PWM_CLOCK_HZ    1000000u
#define HAL_PWM_CYCLE       1000u /* 1 kHz: no visible flicker */

typedef struct {
    hal_gpio_pin_t   pin;
    uint8_t          channel;
    GPIO_FuncTypeDef func;
} hal_pwm_pin_t;

static const hal_pwm_pin_t hal_pwm_pins[] = {
    { GPIO_PA2, PWM0_ID, AS_PWM0 }, { GPIO_PA3, PWM1_ID, AS_PWM1 },
    { GPIO_PA4, PWM2_ID, AS_PWM2 }, { GPIO_PB0, PWM3_ID, AS_PWM3 },
    { GPIO_PB1, PWM4_ID, AS_PWM4 }, { GPIO_PB2, PWM5_ID, AS_PWM5 },
    { GPIO_PB4, PWM4_ID, AS_PWM4 }, { GPIO_PB5, PWM5_ID, AS_PWM5 },
    { GPIO_PC1, PWM0_ID, AS_PWM0 }, { GPIO_PC2, PWM0_ID, AS_PWM0 },
    { GPIO_PC3, PWM1_ID, AS_PWM1 }, { GPIO_PC4, PWM2_ID, AS_PWM2 },
    { GPIO_PD2, PWM3_ID, AS_PWM3 }, { GPIO_PD5, PWM0_ID, AS_PWM0 },
};

#define HAL_PWM_CHANNELS    6
static hal_gpio_pin_t hal_pwm_owner[HAL_PWM_CHANNELS] = {
    HAL_INVALID_PIN, HAL_INVALID_PIN, HAL_INVALID_PIN,
    HAL_INVALID_PIN, HAL_INVALID_PIN, HAL_INVALID_PIN,
};
static uint8_t        hal_pwm_active[HAL_PWM_CHANNELS];
static uint16_t       hal_pwm_high[HAL_PWM_CHANNELS];  // 0: never programmed
static uint8_t        hal_pwm_conflict_logged[HAL_PWM_CHANNELS];
static uint8_t        hal_pwm_clock_ready;

static const hal_pwm_pin_t *hal_pwm_for_pin(hal_gpio_pin_t pin) {
    for (uint8_t i = 0; i < sizeof(hal_pwm_pins) / sizeof(hal_pwm_pins[0]);
         i++) {
        if (hal_pwm_pins[i].pin != pin)
            continue;
        uint8_t        ch    = hal_pwm_pins[i].channel;
        hal_gpio_pin_t owner = hal_pwm_owner[ch];
        if (owner != HAL_INVALID_PIN && owner != pin) {
            /* Channel already drives another LED: this one stays at full
             * brightness. No BSEED board shares a channel (PM C3/B4, non-PM
             * C2/D2), but say so once on boards that do. */
            if (!hal_pwm_conflict_logged[ch]) {
                hal_pwm_conflict_logged[ch] = 1;
                printf("LED: pin 0x%x shares PWM%u, not dimmed\r\n",
                       (unsigned)pin, (unsigned)ch);
            }
            return NULL;
        }

        return &hal_pwm_pins[i];
    }
    return NULL;
}

void hal_gpio_write_duty(hal_gpio_pin_t gpio_pin, uint8_t value,
                         uint8_t duty_pct) {
    const hal_pwm_pin_t *pwm = duty_pct < 100 ? hal_pwm_for_pin(gpio_pin) : NULL;

    if (pwm == NULL) {
        /* Full level. Write the level before handing the pin back to GPIO
         * so leaving PWM never glitches through the opposite level. */
        hal_gpio_write(gpio_pin, value);
        for (uint8_t ch = 0; ch < HAL_PWM_CHANNELS; ch++) {
            if (hal_pwm_owner[ch] == gpio_pin && hal_pwm_active[ch]) {
                gpio_set_func((GPIO_PinTypeDef)gpio_pin, AS_GPIO);
                pwm_stop((pwm_id)ch);
                hal_pwm_active[ch] = 0;
            }
        }
        return;
    }

    if (duty_pct == 0)
        duty_pct = 1;
    if (!hal_pwm_clock_ready) {
        pwm_set_clk(CLOCK_SYS_CLOCK_HZ, HAL_PWM_CLOCK_HZ);
        hal_pwm_clock_ready = 1;
    }
    /* Normal mode: high for cmp ticks, then low until the cycle ends. */
    uint16_t high = (uint16_t)((HAL_PWM_CYCLE * duty_pct) / 100u);
    if (!value)
        high = (uint16_t)(HAL_PWM_CYCLE - high);
    pwm_id id = (pwm_id)pwm->channel;
    if (hal_pwm_high[pwm->channel] != high) {
        /* Program only when the duty changes (claim or brightness write),
         * not on every blink. */
        if (hal_pwm_high[pwm->channel] == 0) {
            pwm_set_mode(id, PWM_NORMAL_MODE);
            pwm_set_phase(id, 0);
        }
        pwm_set_cycle_and_duty(id, HAL_PWM_CYCLE, high);
        hal_pwm_high[pwm->channel] = high;
    }
    if (!hal_pwm_active[pwm->channel]) {
        pwm_start(id);
        gpio_set_func((GPIO_PinTypeDef)gpio_pin, pwm->func);
        hal_pwm_owner[pwm->channel]  = gpio_pin;
        hal_pwm_active[pwm->channel] = 1;
    }
    /* Retention bookkeeping keeps the logical level (unused without PM). */
    gpio_config_t *config = find_gpio_config(gpio_pin);
    if (config)
        config->value = value ? 1 : 0;
}

#else
void hal_gpio_write_duty(hal_gpio_pin_t gpio_pin, uint8_t value,
                         uint8_t duty_pct) {
    (void)duty_pct;
    hal_gpio_write(gpio_pin, value);
}

#endif

uint8_t hal_gpio_read(hal_gpio_pin_t gpio_pin) {
    return gpio_read((GPIO_PinTypeDef)gpio_pin) ? 1 : 0;
}

hal_gpio_pin_t hal_gpio_parse_pin(const char *s) {
    if (!s || strlen(s) < 2) {
        return HAL_INVALID_PIN;
    }

    // Parse format like "A5", "B3", etc.
    char port = s[0];
    int  pin  = atoi(&s[1]);

    if (pin < 0 || pin > 7) {
        return HAL_INVALID_PIN;
    }

    // Return direct Telink GPIO_PinTypeDef values
    uint16_t port_base;
    switch (port) {
    case 'A':
    case 'a':
        port_base = GPIO_GROUPA;
        break;
    case 'B':
    case 'b':
        port_base = GPIO_GROUPB;
        break;
    case 'C':
    case 'c':
        port_base = GPIO_GROUPC;
        break;
    case 'D':
    case 'd':
        port_base = GPIO_GROUPD;
        break;
    case 'E':
    case 'e':
        port_base = GPIO_GROUPE;
        break;
    default:
        return HAL_INVALID_PIN;
    }

    return (hal_gpio_pin_t)(port_base | BIT(pin));
}

hal_gpio_pull_t hal_gpio_parse_pull(const char *pull_str) {
    if (!pull_str) {
        return HAL_GPIO_PULL_INVALID;
    }

    switch (pull_str[0]) {
    case 'u':
        return HAL_GPIO_PULL_UP;

    case 'U':
        return HAL_GPIO_PULL_UP_1M;

    case 'd':
    case 'D':
        return HAL_GPIO_PULL_DOWN;

    case 'f':
    case 'F':
    case 'n':
    case 'N':
        return HAL_GPIO_PULL_NONE;

    default:
        return HAL_GPIO_PULL_INVALID;
    }
}

// GPIO interrupt callbacks
