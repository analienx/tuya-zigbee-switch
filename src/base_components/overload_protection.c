#include "overload_protection.h"

#define STARTUP_MODE_OFF    0x00

void overload_protection_init(overload_protection_t *op) {
    if (!op)
        return;

    op->cfg.power_limit_w     = 2500;
    op->cfg.current_limit_ma  = 10000;
    op->cfg.trip_delay_s      = 30;
    op->cfg.overvoltage_cv    = 26000;
    op->cfg.undervoltage_cv   = 21000;
    op->cfg.reconnect_delay_s = 60;
    op->cfg.hard_power_w      = OVERLOAD_HARD_POWER_W;
    op->cfg.hard_current_ma   = OVERLOAD_HARD_CURRENT_MA;

    op->alarm           = OVERLOAD_ALARM_NONE;
    op->tripped         = 0;
    op->locked_out      = 0;
    op->retry_count     = 0;
    op->over_since_ms   = 0;
    op->reconnect_at_ms = 0;
}

void overload_protection_set_current_limits(overload_protection_t *op,
                                            uint16_t soft_current_ma,
                                            uint16_t hard_current_ma) {
    if (!op)
        return;

    if (soft_current_ma) {
        op->cfg.current_limit_ma = soft_current_ma;
        op->cfg.power_limit_w =
            (uint16_t)((uint32_t)soft_current_ma * OVERLOAD_NOMINAL_VOLTAGE_V /
                       1000u);
    }
    if (hard_current_ma) {
        op->cfg.hard_current_ma = hard_current_ma;
        op->cfg.hard_power_w =
            (uint16_t)((uint32_t)hard_current_ma * OVERLOAD_NOMINAL_VOLTAGE_V /
                       1000u);
    }
}

static uint32_t stamp(uint32_t now) {
    return now == 0 ? 1 : now;
}

static uint8_t reached(uint32_t now, uint32_t target) {
    return (int32_t)(now - target) >= 0;
}

static overload_action_t trip(overload_protection_t *op, uint32_t now,
                              overload_alarm_t reason, uint8_t startup_mode) {
    op->tripped       = 1;
    op->over_since_ms = 0;

    if (op->retry_count >= OVERLOAD_MAX_RETRIES) {
        op->locked_out      = 1;
        op->alarm           = OVERLOAD_ALARM_LOCKED_OUT;
        op->reconnect_at_ms = 0;
    } else if (startup_mode != STARTUP_MODE_OFF) {
        op->alarm           = reason;
        op->reconnect_at_ms = stamp(now + op->cfg.reconnect_delay_s * 1000u);
    } else {
        op->alarm           = reason;
        op->reconnect_at_ms = 0;
    }
    return OVERLOAD_ACTION_TURN_OFF;
}

overload_action_t overload_protection_check(overload_protection_t *op,
                                            uint32_t now_ms,
                                            uint16_t voltage_cv,
                                            uint16_t current_ma,
                                            int32_t power_w,
                                            uint8_t relay_is_on,
                                            uint8_t startup_mode) {
    if (!op)
        return OVERLOAD_ACTION_NONE;

    if (op->tripped && relay_is_on) {
        op->tripped         = 0;
        op->locked_out      = 0;
        op->retry_count     = 0;
        op->over_since_ms   = 0;
        op->reconnect_at_ms = 0;
        op->alarm           = OVERLOAD_ALARM_NONE;
    }

    if (op->locked_out) {
        op->alarm = OVERLOAD_ALARM_LOCKED_OUT;
        return relay_is_on ? OVERLOAD_ACTION_TURN_OFF : OVERLOAD_ACTION_NONE;
    }

    if (op->tripped && !relay_is_on) {
        if (startup_mode != STARTUP_MODE_OFF && op->reconnect_at_ms != 0 &&
            reached(now_ms, op->reconnect_at_ms)) {
            op->retry_count++;
            op->tripped         = 0;
            op->over_since_ms   = 0;
            op->reconnect_at_ms = 0;
            return OVERLOAD_ACTION_TURN_ON;
        }
        return OVERLOAD_ACTION_NONE;
    }

    if (!relay_is_on) {
        op->over_since_ms = 0;
        op->alarm         = OVERLOAD_ALARM_NONE;
        return OVERLOAD_ACTION_NONE;
    }

    uint8_t peak =
        (op->cfg.hard_power_w != 0 && power_w >= (int32_t)op->cfg.hard_power_w) ||
        (op->cfg.hard_current_ma != 0 && current_ma >= op->cfg.hard_current_ma);
    if (peak) {
        return trip(op, now_ms, OVERLOAD_ALARM_PEAK, startup_mode);
    }

    uint8_t soft_power = (op->cfg.power_limit_w != 0) &&
                         (power_w >= (int32_t)op->cfg.power_limit_w);
    uint8_t soft_current = (op->cfg.current_limit_ma != 0) &&
                           (current_ma >= op->cfg.current_limit_ma);

    if (soft_power || soft_current) {
        if (op->over_since_ms == 0) {
            op->over_since_ms = stamp(now_ms);
        }
        op->alarm = soft_power ? OVERLOAD_ALARM_POWER : OVERLOAD_ALARM_CURRENT;
        if ((uint32_t)(now_ms - op->over_since_ms) >=
            op->cfg.trip_delay_s * 1000u) {
            return trip(op, now_ms, op->alarm, startup_mode);
        }
        return OVERLOAD_ACTION_NONE;
    }

    op->over_since_ms = 0;
    if (op->cfg.overvoltage_cv != 0 && voltage_cv > op->cfg.overvoltage_cv) {
        op->alarm = OVERLOAD_ALARM_VOLTAGE_HIGH;
    } else if (op->cfg.undervoltage_cv != 0 &&
               voltage_cv > OVERLOAD_VOLTAGE_FLOOR_CV &&
               voltage_cv < op->cfg.undervoltage_cv) {
        op->alarm = OVERLOAD_ALARM_VOLTAGE_LOW;
    } else {
        op->alarm = OVERLOAD_ALARM_NONE;
    }
    return OVERLOAD_ACTION_NONE;
}
