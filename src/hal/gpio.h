#ifndef _HAL_GPIO_H_
#define _HAL_GPIO_H_

#include <stdint.h>

#define HAL_INVALID_PIN    0xFFFF

typedef uint16_t hal_gpio_pin_t;

typedef enum {
    HAL_GPIO_PULL_NONE    = 0,
    HAL_GPIO_PULL_UP      = 1,
    HAL_GPIO_PULL_UP_1M   = 2,
    HAL_GPIO_PULL_DOWN    = 3,
    HAL_GPIO_PULL_INVALID = 0xFF,
} hal_gpio_pull_t;

void hal_gpio_init(hal_gpio_pin_t gpio_pin, uint8_t is_input,
                   hal_gpio_pull_t pull);

void hal_gpio_init_output(hal_gpio_pin_t gpio_pin, hal_gpio_pull_t pull,
                          uint8_t initial_value);

void hal_gpio_set(hal_gpio_pin_t gpio_pin);
void hal_gpio_clear(hal_gpio_pin_t gpio_pin);

static inline void hal_gpio_write(hal_gpio_pin_t gpio_pin, uint8_t value) {
    if (value) {
        hal_gpio_set(gpio_pin);
    } else {
        hal_gpio_clear(gpio_pin);
    }
}

uint8_t hal_gpio_read(hal_gpio_pin_t gpio_pin);

typedef void (*gpio_callback_t)(hal_gpio_pin_t gpio_pin, void *arg);

void hal_gpio_callback(hal_gpio_pin_t gpio_pin, gpio_callback_t callback,
                       void *arg);
void hal_gpio_unreg_callback(hal_gpio_pin_t gpio_pin);

hal_gpio_pin_t hal_gpio_parse_pin(const char *s);
hal_gpio_pull_t hal_gpio_parse_pull(const char *pull_str);

/* Hardware GPIO pulse counter API. The BSEED BL0937 needs two independent
 * counters (CF and CF1); Telink maps them to timer0/timer1. */
#define HAL_GPIO_COUNTER_INVALID    -1

typedef int8_t hal_gpio_counter_t;

typedef enum {
    HAL_GPIO_COUNTER_RISING  = 0,
    HAL_GPIO_COUNTER_FALLING = 1,
} hal_gpio_counter_edge_t;

hal_gpio_counter_t hal_gpio_counter_init(hal_gpio_pin_t gpio_pin,
                                         hal_gpio_counter_edge_t edge,
                                         hal_gpio_pull_t pull);
void hal_gpio_counter_deinit(hal_gpio_counter_t counter);
uint32_t hal_gpio_counter_read(hal_gpio_counter_t counter);
void hal_gpio_counter_reset(hal_gpio_counter_t counter);
void hal_gpio_counter_start(hal_gpio_counter_t counter);
void hal_gpio_counter_stop(hal_gpio_counter_t counter);

static inline uint32_t hal_gpio_counter_read_and_reset(
    hal_gpio_counter_t counter) {
    hal_gpio_counter_stop(counter);
    uint32_t count = hal_gpio_counter_read(counter);
    hal_gpio_counter_reset(counter);
    hal_gpio_counter_start(counter);
    return count;
}

#endif
