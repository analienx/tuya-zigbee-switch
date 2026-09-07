#ifndef _OVERLOAD_PROTECTION_H_
#define _OVERLOAD_PROTECTION_H_

#include <stdint.h>

#define OVERLOAD_HARD_POWER_W         3680u
#define OVERLOAD_HARD_CURRENT_MA      16000u
#define OVERLOAD_MAX_RETRIES          5u
#define OVERLOAD_NOMINAL_VOLTAGE_V    230u
#define OVERLOAD_VOLTAGE_FLOOR_CV     5000u

typedef enum {
    OVERLOAD_ALARM_NONE         = 0,
    OVERLOAD_ALARM_POWER        = 1,
    OVERLOAD_ALARM_CURRENT      = 2,
    OVERLOAD_ALARM_PEAK         = 3,
    OVERLOAD_ALARM_VOLTAGE_HIGH = 4,
    OVERLOAD_ALARM_VOLTAGE_LOW  = 5,
    OVERLOAD_ALARM_LOCKED_OUT   = 6,
} overload_alarm_t;

typedef enum {
    OVERLOAD_ACTION_NONE     = 0,
    OVERLOAD_ACTION_TURN_OFF = 1,
    OVERLOAD_ACTION_TURN_ON  = 2,
} overload_action_t;

typedef struct {
    uint16_t power_limit_w;
    uint16_t current_limit_ma;
    uint16_t trip_delay_s;
    uint16_t overvoltage_cv;
    uint16_t undervoltage_cv;
    uint16_t reconnect_delay_s;
    uint16_t hard_power_w;
    uint16_t hard_current_ma;
} overload_config_t;

typedef struct {
    overload_config_t cfg;
    overload_alarm_t  alarm;
    uint8_t           tripped;
    uint8_t           locked_out;
    uint8_t           retry_count;
    uint32_t          over_since_ms;
    uint32_t          reconnect_at_ms;
} overload_protection_t;

void overload_protection_init(overload_protection_t *op);
void overload_protection_set_current_limits(overload_protection_t *op,
                                            uint16_t soft_current_ma,
                                            uint16_t hard_current_ma);
overload_action_t overload_protection_check(overload_protection_t *op,
                                            uint32_t now_ms,
                                            uint16_t voltage_cv,
                                            uint16_t current_ma,
                                            int32_t power_w,
                                            uint8_t relay_is_on,
                                            uint8_t startup_mode);

#endif /* _OVERLOAD_PROTECTION_H_ */
