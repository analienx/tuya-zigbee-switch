#include "led.h"

#include "hal/gpio.h"
#include "hal/tasks.h"
#include "hal/timer.h"

#include <stdio.h>

static uint8_t led_brightness = LED_DEFAULT_BRIGHTNESS;

uint8_t led_clamp_brightness(uint8_t percent) {
    if (percent < LED_MIN_BRIGHTNESS)
        return LED_MIN_BRIGHTNESS;

    if (percent > LED_MAX_BRIGHTNESS)
        return LED_MAX_BRIGHTNESS;

    return percent;
}

void led_set_brightness(uint8_t percent) {
    led_brightness = led_clamp_brightness(percent);
}

uint8_t led_get_brightness(void) {
    return led_brightness;
}

/* Lit: the LED's active level for `brightness` percent of each PWM period.
 * Dark: the inactive level at full drive, so an off LED never glows. */
static void led_drive(led_t *led, uint8_t lit) {
#ifdef LED_DIMMING
    if (lit)
        hal_gpio_write_duty(led->pin, led->on_high, led_brightness);
    else
        hal_gpio_write_duty(led->pin, !led->on_high, LED_MAX_BRIGHTNESS);
#else
    hal_gpio_write(led->pin, lit ? led->on_high : !led->on_high);
#endif
}

void led_refresh(led_t *led) {
    led_drive(led, led->on);
}

void led_init(led_t *led) {
    led_off(led);
}

void led_on(led_t *led) {
    led_drive(led, 1);
    led->on = 1;
    led->blink_times_left = 0;
}

void led_off(led_t *led) {
    led_drive(led, 0);
    led->on = 0;
    led->blink_times_left = 0;
}

static void led_blink_handler(void *arg) {
    led_t *led = (led_t *)arg;

    if (led->blink_times_left == 0)
        return;

    if (led->on) {
        led->on = 0;
        led_drive(led, 0);
        if (led->blink_times_left != LED_BLINK_FOREVER) {
            led->blink_times_left--;
        }
        hal_tasks_schedule(&led->blink_task, led->blink_time_off);
    } else {
        led->on = 1;
        led_drive(led, 1);
        hal_tasks_schedule(&led->blink_task, led->blink_time_on);
    }
}

void led_blink(led_t *led, uint16_t on_time_ms, uint16_t off_time_ms,
               uint16_t times) {
    // Always set new durations
    led->blink_time_on  = on_time_ms;
    led->blink_time_off = off_time_ms;

    if (led->blink_times_left != 0) {
        // If we already blinking, do not reschedule
        // to unnecessary avoid jumps in blinking pace
        led->blink_times_left = times;
        return;
    }

    // led_on()/led_off() end a blink without cancelling its timer, which stays
    // linked until it next fires; never re-init an event still in the list.
    if (led->blink_task.handler == led_blink_handler)
        hal_tasks_unschedule(&led->blink_task);

    led_drive(led, 1);
    led->on = 1;
    led->blink_times_left   = times;
    led->blink_task.handler = led_blink_handler;
    led->blink_task.arg     = led;
    hal_tasks_init(&led->blink_task);
    hal_tasks_schedule(&led->blink_task, on_time_ms);
}

void led_stop(led_t *led) {
    // Only a task led_blink() initialized can be linked into the timer list.
    if (led->blink_task.handler == led_blink_handler)
        hal_tasks_unschedule(&led->blink_task);
    led_off(led);
}
