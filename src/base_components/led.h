#ifndef _LED_H_
#define _LED_H_

#include "hal/gpio.h"
#include "hal/tasks.h"
#include <stdint.h>

typedef struct {
    hal_gpio_pin_t pin;
    uint8_t        on_high;
    uint8_t        on;
    uint16_t       blink_times_left;
    uint16_t       blink_time_on;
    uint16_t       blink_time_off;
    hal_task_t     blink_task;
} led_t;

/**
 * @brief      Initialize led (set initial state)
 * @param	   *led - Led to use
 * @return     none
 */
void led_init(led_t *led);

/**
 * @brief      Turn on led, canceling any blinking
 * @param	   *led - Led to use
 * @return     none
 */
void led_on(led_t *led);

/**
 * @brief      Turn off led, canceling any blinking
 * @param	   *led - Led to use
 * @return     none
 */
void led_off(led_t *led);

#define LED_BLINK_FOREVER    0xFFFF

/* Brightness of every LED while lit, percent of full drive (1..100). Only
 * LED_DIMMING builds (Telink hardware PWM, never with PM_ENABLE) apply it;
 * every other build drives plain on/off GPIO. A second LED on a PWM channel
 * that another LED already uses stays at full brightness (logged once). */
#ifndef LED_DEFAULT_BRIGHTNESS
#define LED_DEFAULT_BRIGHTNESS    100
#endif
#define LED_MIN_BRIGHTNESS        1
#define LED_MAX_BRIGHTNESS        100

/**
 * @brief      Set the brightness used by every LED while lit (clamped to
 *             1..100). Call led_refresh() on lit LEDs to apply it at once.
 */
void led_set_brightness(uint8_t percent);

/** The brightness led_set_brightness() would apply for `percent`. */
uint8_t led_clamp_brightness(uint8_t percent);

uint8_t led_get_brightness(void);

/**
 * @brief      Re-drive the LED's current level (e.g. after a brightness
 *             change) without touching its blink state.
 */
void led_refresh(led_t *led);

/**
 * @brief      Start led blinking, will go to off when finished
 * @param	     *led - Led to use
 *             on_time_ms - Time led should be on in milliseconds
 *             off_time_ms - Time led should be off in milliseconds
 *             times - Times to repeat blink before returning to fixed state,
 *                     0xFFFF - blink forever
 * @return     none
 */
void led_blink(led_t *led, uint16_t on_time_ms, uint16_t off_time_ms,
               uint16_t times);

/**
 * @brief      Turn off led and cancel its pending blink timer, so a following
 *             led_blink() starts a new phase
 * @param	   *led - Led to use
 * @return     none
 */
void led_stop(led_t *led);

#endif
