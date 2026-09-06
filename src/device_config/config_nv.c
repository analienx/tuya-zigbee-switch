#include "config_nv.h"
#include "hal/gpio.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"
#include <stddef.h>
#include <string.h>

#ifdef HAL_SILABS
#include "silabs_config.h"
#endif

#ifndef STRINGIFY
#define _STRINGIFY(x)    #x
#define STRINGIFY(x)     _STRINGIFY(x)
#endif

#ifndef DEFAULT_CONFIG
const char default_config_data[] = "unknown;TS0012-CUSTOM;";
#else
const char default_config_data[] = STRINGIFY(DEFAULT_CONFIG);
#endif

static const char emergency_config_data[] = "unknown;TS0012-CUSTOM;";

device_config_str_t device_config_str;
static bool         parser_preflight_enabled = false;

static bool config_digits_only(const uint8_t *data, uint16_t start,
                               uint16_t len) {
    if (len == 0) {
        return false;
    }

    for (uint16_t i = 0; i < len; i++) {
        if (data[start + i] < '0' || data[start + i] > '9') {
            return false;
        }
    }

    return true;
}

static bool config_decimal_fits(const uint8_t *data, uint16_t start,
                                uint16_t len, uint32_t max_value) {
    if (!config_digits_only(data, start, len)) {
        return false;
    }

    uint32_t value = 0;
    for (uint16_t i = 0; i < len; i++) {
        uint32_t digit = (uint32_t)(data[start + i] - '0');
        if (value > (max_value - digit) / 10u) {
            return false;
        }
        value = value * 10u + digit;
    }
    return true;
}

static bool config_token_equals(const uint8_t *data, uint16_t start,
                                uint16_t len, const char *expected) {
    size_t expected_len = strlen(expected);

    return len == expected_len &&
           memcmp(&data[start], expected, expected_len) == 0;
}

static bool config_pin_is_valid(const uint8_t *data, uint16_t start) {
    char pin[3] = { (char)data[start], (char)data[start + 1], '\0' };

    return hal_gpio_parse_pin(pin) != HAL_INVALID_PIN;
}

static bool config_pin_pair_equal(const uint8_t *data, uint16_t a,
                                  uint16_t b) {
    return data[a] == data[b] && data[a + 1] == data[b + 1];
}

static bool config_pull_is_valid(uint8_t pull) {
    switch (pull) {
    case 'u':
    case 'U':
    case 'd':
    case 'D':
    case 'f':
    case 'F':
    case 'n':
    case 'N':
        return true;

    default:
        return false;
    }
}

/* EP<CF><CF1><SEL>[I][V<n>][A<n>][W<n>]
 *
 * This validator runs before parse_config and therefore before pulse-counter or
 * SEL GPIO initialization. Exactly one marker of each kind is allowed and all
 * numeric multipliers must fit uint32_t. */
static bool config_pulse_meter_token_valid(const uint8_t *data, uint16_t start,
                                           uint16_t len) {
    if (len < 8 || data[start] != 'E' || data[start + 1] != 'P' ||
        !config_pin_is_valid(data, start + 2) ||
        !config_pin_is_valid(data, start + 4) ||
        !config_pin_is_valid(data, start + 6)) {
        return false;
    }

    if (config_pin_pair_equal(data, start + 2, start + 4) ||
        config_pin_pair_equal(data, start + 2, start + 6) ||
        config_pin_pair_equal(data, start + 4, start + 6)) {
        return false;
    }

    bool     seen_i = false;
    bool     seen_v = false;
    bool     seen_a = false;
    bool     seen_w = false;
    uint16_t pos    = 8;

    while (pos < len) {
        uint8_t marker = data[start + pos++];
        if (marker == 'I') {
            if (seen_i) {
                return false;
            }
            seen_i = true;
            continue;
        }

        bool *seen = NULL;
        if (marker == 'V') {
            seen = &seen_v;
        } else if (marker == 'A') {
            seen = &seen_a;
        } else if (marker == 'W') {
            seen = &seen_w;
        } else {
            return false;
        }

        if (*seen) {
            return false;
        }
        *seen = true;

        uint16_t digits_start = pos;
        while (pos < len && data[start + pos] >= '0' &&
               data[start + pos] <= '9') {
            pos++;
        }
        if (!config_decimal_fits(data, start + digits_start,
                                 pos - digits_start, UINT32_MAX)) {
            return false;
        }
    }

    return true;
}

/* OL[C<soft-mA>][P<hard-mA>] -- at least one setting, no duplicates. */
static bool config_overload_token_valid(const uint8_t *data, uint16_t start,
                                        uint16_t len) {
    if (len < 4 || data[start] != 'O' || data[start + 1] != 'L') {
        return false;
    }

    bool     seen_c = false;
    bool     seen_p = false;
    uint16_t pos    = 2;
    while (pos < len) {
        uint8_t marker = data[start + pos++];
        bool *  seen   = NULL;
        if (marker == 'C') {
            seen = &seen_c;
        } else if (marker == 'P') {
            seen = &seen_p;
        } else {
            return false;
        }

        if (*seen) {
            return false;
        }
        *seen = true;

        uint16_t digits_start = pos;
        while (pos < len && data[start + pos] >= '0' &&
               data[start + pos] <= '9') {
            pos++;
        }
        if (!config_decimal_fits(data, start + digits_start,
                                 pos - digits_start, UINT16_MAX)) {
            return false;
        }
    }

    return seen_c || seen_p;
}

static bool config_structurally_valid(const uint8_t *data, uint16_t size) {
    if (data == NULL || size < 4 || size >= sizeof(device_config_str.data)) {
        return false;
    }
    if (data[size - 1] != ';') {
        return false;
    }

    uint16_t separators             = 0;
    bool     previous_was_separator = false;
    for (uint16_t i = 0; i < size; i++) {
        uint8_t ch = data[i];
        if (ch < 0x20 || ch > 0x7e) {
            return false;
        }
        if (ch == ';') {
            if (i == 0 || previous_was_separator) {
                return false;
            }
            separators++;
            previous_was_separator = true;
        } else {
            previous_was_separator = false;
        }
    }

    return separators >= 2;
}

static void load_config_copy(const char *config) {
    size_t len = strlen(config);

    memset(&device_config_str, 0, sizeof(device_config_str));
    if (len >= sizeof(device_config_str.data)) {
        len = sizeof(device_config_str.data) - 1;
    }
    memcpy(device_config_str.data, config, len);
    device_config_str.size = (uint16_t)len;
}

void device_config_write_to_nv() {
    if (!device_config_is_valid(device_config_str.data, device_config_str.size)) {
        printf("Refusing to write invalid/unsafe device config\r\n");
        return;
    }

    printf("Writing config to nv: %s\r\n", device_config_str.data);
    hal_nvm_status_t st = 0;

    printf("Size: %d\r\n", (int)sizeof(device_config_str));
    st = hal_nvm_write(NV_ITEM_DEVICE_CONFIG, sizeof(device_config_str),
                       (uint8_t *)&device_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf(
            "Failed to write DEVICE_CONFIG_DATA to NV, status: %d. (bytes: %d)\r\n",
            st, device_config_str.size);
    } else {
        printf("success!\r\n");
    }
}

void device_config_read_raw_from_nv() {
    hal_nvm_status_t st = 0;

    st = hal_nvm_read(NV_ITEM_DEVICE_CONFIG, sizeof(device_config_str),
                      (uint8_t *)&device_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf("Failed to read NV_ITEM_DEVICE_CONFIG, using default config "
               "instead, status: %d. (bytes: %d)\r\n",
               st, device_config_str.size);
        load_config_copy(default_config_data);
    }
}

void device_config_enable_parser_preflight(void) {
    parser_preflight_enabled = true;
}

void device_config_read_from_nv() {
    device_config_read_raw_from_nv();

    if (parser_preflight_enabled && !device_config_prepare_for_parse()) {
        printf("Unable to prepare a parser-safe device config\r\n");
        load_config_copy(emergency_config_data);
    }

    printf("Using config: %d chars from\r\n%s\r\n", device_config_str.size,
           device_config_str.data);
}

bool device_config_resources_are_safe(const uint8_t *data, uint16_t size) {
    if (!config_structurally_valid(data, size)) {
        return false;
    }

    uint16_t buttons               = 0;
    uint16_t leds                  = 0;
    uint16_t relays                = 0;
    uint16_t switch_clusters       = 0;
    uint16_t relay_clusters        = 0;
    uint16_t cover_switch_clusters = 0;
    uint16_t cover_clusters        = 0;
    uint16_t battery_tokens        = 0;
    uint16_t meter_tokens          = 0;
    uint16_t overload_tokens       = 0;
    uint16_t token_start           = 0;
    uint16_t token_index           = 0;
#ifdef BSEED_PM_B28WRPVX
    bool bseed_pm_manufacturer = false;
    bool bseed_pm_model        = false;
#endif

    for (uint16_t cursor = 0; cursor < size; cursor++) {
        if (data[cursor] != ';') {
            continue;
        }

        uint16_t len = cursor - token_start;
        if (token_index < 2) {
            if (len == 0 || len > 31) {
                return false;
            }
#ifdef BSEED_PM_B28WRPVX
            if (token_index == 0) {
                bseed_pm_manufacturer =
                    config_token_equals(data, token_start, len, "b28wrpvx");
            } else {
                bseed_pm_model = config_token_equals(data, token_start, len,
                                                     "TS011F-BS-PM");
            }
#endif
        } else {
            uint8_t kind = data[token_start];

            if (config_token_equals(data, token_start, len, "SLP") ||
                config_token_equals(data, token_start, len, "M")) {
                // Stateless parser modifiers.
            } else if (kind == 'D') {
                if (len < 2 ||
                    !config_digits_only(data, token_start + 1, len - 1)) {
                    return false;
                }
            } else if (kind == 'B' && len >= 2 &&
                       data[token_start + 1] == 'T') {
                if (len != 4 ||
                    !config_pin_is_valid(data, token_start + 2)) {
                    return false;
                }
                battery_tokens++;
            } else if (kind == 'B') {
                if (len != 4 ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    !config_pull_is_valid(data[token_start + 3])) {
                    return false;
                }
                buttons++;
            } else if (kind == 'L' || kind == 'I') {
                if ((len != 3 && len != 4) ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    (len == 4 && data[token_start + 3] != 'i')) {
                    return false;
                }
                leds++;
            } else if (kind == 'S') {
                if (len != 4 ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    !config_pull_is_valid(data[token_start + 3])) {
                    return false;
                }
                buttons++;
                switch_clusters++;
            } else if (kind == 'R') {
                if ((len != 3 && len != 5) ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    (len == 5 &&
                     !config_pin_is_valid(data, token_start + 3))) {
                    return false;
                }
                relays++;
                relay_clusters++;
            } else if (kind == 'X') {
                if (len != 6 ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    !config_pin_is_valid(data, token_start + 3) ||
                    !config_pull_is_valid(data[token_start + 5])) {
                    return false;
                }
                buttons += 2;
                cover_switch_clusters++;
            } else if (kind == 'C') {
                if (len != 5 ||
                    !config_pin_is_valid(data, token_start + 1) ||
                    !config_pin_is_valid(data, token_start + 3)) {
                    return false;
                }
                relays += 2;
                cover_clusters++;
            } else if (kind == 'i') {
                if (len < 2 ||
                    !config_digits_only(data, token_start + 1, len - 1)) {
                    return false;
                }
            } else if (kind == 'E' && len >= 2 &&
                       data[token_start + 1] == 'P') {
                if (!config_pulse_meter_token_valid(data, token_start, len)) {
                    return false;
                }
                meter_tokens++;
            } else if (kind == 'O' && len >= 2 &&
                       data[token_start + 1] == 'L') {
                if (!config_overload_token_valid(data, token_start, len)) {
                    return false;
                }
                overload_tokens++;

                /* OL is order-sensitive in parse_config. An explicit meter
                 * must precede it, except for the exact dedicated BSEED PM
                 * build where the implicit meter is initialized before tokens. */
                bool meter_available = meter_tokens != 0;
#ifdef BSEED_PM_B28WRPVX
                meter_available = meter_available ||
                                  (bseed_pm_manufacturer && bseed_pm_model);
#endif
                if (!meter_available) {
                    return false;
                }
            } else {
                // Preserve generic/upstream compatibility for unknown tokens.
            }
        }

        token_index++;
        token_start = cursor + 1;
    }

    uint16_t logical_meters = meter_tokens;
#ifdef BSEED_PM_B28WRPVX
    if (logical_meters == 0 && bseed_pm_manufacturer && bseed_pm_model) {
        logical_meters = 1;
    }
#endif

    if (battery_tokens > 1 || meter_tokens > 1 || logical_meters > 1 ||
        overload_tokens > 1 ||
        buttons > DEVICE_CONFIG_MAX_BUTTONS ||
        leds > DEVICE_CONFIG_MAX_LEDS ||
        relays > DEVICE_CONFIG_MAX_RELAYS ||
        switch_clusters > DEVICE_CONFIG_MAX_SWITCH_CLUSTERS ||
        relay_clusters > DEVICE_CONFIG_MAX_RELAY_CLUSTERS ||
        cover_switch_clusters > MAX_COVER_SWITCHES ||
        cover_clusters > MAX_COVERS) {
        return false;
    }

    uint16_t total_endpoints = switch_clusters + relay_clusters +
                               cover_switch_clusters + cover_clusters;
    if (total_endpoints == 0) {
        total_endpoints = 1;
    }
    if (total_endpoints > DEVICE_CONFIG_MAX_ENDPOINTS) {
        return false;
    }

    // Endpoint 1 owns Basic + OTA. Metering adds Electrical Measurement and
    // Metering to an existing endpoint; it consumes two cluster-pool entries
    // but no new endpoint.
    uint16_t total_clusters = 2 + switch_clusters * 4 + relay_clusters * 3 +
                              cover_switch_clusters * 3 + cover_clusters +
                              logical_meters * 2;
    if (battery_tokens != 0) {
        total_clusters++;
    }
#ifdef END_DEVICE
    total_clusters++;
#endif

    return total_clusters <= DEVICE_CONFIG_CLUSTER_POOL_SIZE;
}

bool device_config_prepare_for_parse(void) {
    if (device_config_resources_are_safe(device_config_str.data,
                                         device_config_str.size)) {
        return true;
    }

    printf("Stored device config is unsafe; using compiled default in RAM\r\n");
    load_config_copy(default_config_data);
    if (device_config_resources_are_safe(device_config_str.data,
                                         device_config_str.size)) {
        return true;
    }

    printf("Compiled default config is unsafe; using emergency minimal config\r\n");
    load_config_copy(emergency_config_data);
    return device_config_resources_are_safe(device_config_str.data,
                                            device_config_str.size);
}

#ifdef DEVICE_CONFIG_GUARD_BSEED_TS0726_3GANG
static bool bseed_token_equals(const uint8_t *data, uint16_t start,
                               uint16_t len, const char *expected) {
    size_t expected_len = strlen(expected);

    return len == expected_len &&
           memcmp(&data[start], expected, expected_len) == 0;
}

static bool bseed_parse_pin(const uint8_t *data, uint16_t start,
                            uint8_t *pin_id) {
    uint8_t port = data[start];
    uint8_t pin  = data[start + 1];

    if (port < 'A' || port > 'E' || pin < '0' || pin > '7') {
        return false;
    }

    *pin_id = (uint8_t)((port - 'A') * 8 + (pin - '0'));
    return true;
}

static bool bseed_claim_pin(const uint8_t *data, uint16_t start,
                            bool used_pins[40]) {
    uint8_t pin_id;

    if (!bseed_parse_pin(data, start, &pin_id) || used_pins[pin_id]) {
        return false;
    }

    used_pins[pin_id] = true;
    return true;
}

static bool bseed_pull_is_valid(uint8_t pull) {
    switch (pull) {
    case 'u':
    case 'U':
    case 'd':
    case 'D':
    case 'f':
    case 'F':
    case 'n':
    case 'N':
        return true;

    default:
        return false;
    }
}

static bool bseed_digits_only(const uint8_t *data, uint16_t start,
                              uint16_t len) {
    return config_digits_only(data, start, len);
}

static bool bseed_ts0726_3gang_config_is_valid(const uint8_t *data,
                                               uint16_t size) {
    uint8_t  network_count   = 0;
    uint8_t  switch_count    = 0;
    uint8_t  relay_count     = 0;
    uint8_t  indicator_count = 0;
    uint8_t  momentary_count = 0;
    bool     used_pins[40]   = { false };
    uint16_t token_start     = 0;
    uint8_t  token_index     = 0;

    for (uint16_t cursor = 0; cursor < size; cursor++) {
        if (data[cursor] != ';') {
            continue;
        }

        uint16_t len = cursor - token_start;
        if (token_index == 0) {
            if (!bseed_token_equals(data, token_start, len, "iedhxgyi")) {
                return false;
            }
        } else if (token_index == 1) {
            if (!bseed_token_equals(data, token_start, len, "TS0726-3-BS")) {
                return false;
            }
        } else if (bseed_token_equals(data, token_start, len, "M")) {
            momentary_count++;
        } else if (bseed_token_equals(data, token_start, len, "SLP")) {
            // Valid Romasku option: allow simultaneous latching pulses.
        } else if (len >= 2 && data[token_start] == 'D') {
            if (!bseed_digits_only(data, token_start + 1, len - 1)) {
                return false;
            }
        } else if ((len == 3 || len == 4) &&
                   data[token_start] == 'L') {
            if (len == 4 && data[token_start + 3] != 'i') {
                return false;
            }
            if (!bseed_claim_pin(data, token_start + 1, used_pins)) {
                return false;
            }
            network_count++;
        } else if (len == 4 && data[token_start] == 'S') {
            if (!bseed_pull_is_valid(data[token_start + 3]) ||
                !bseed_claim_pin(data, token_start + 1, used_pins)) {
                return false;
            }
            switch_count++;
        } else if ((len == 3 || len == 5) &&
                   data[token_start] == 'R') {
            if (!bseed_claim_pin(data, token_start + 1, used_pins)) {
                return false;
            }
            if (len == 5 &&
                !bseed_claim_pin(data, token_start + 3, used_pins)) {
                return false;
            }
            relay_count++;
        } else if ((len == 3 || len == 4) &&
                   data[token_start] == 'I') {
            if (len == 4 && data[token_start + 3] != 'i') {
                return false;
            }
            if (!bseed_claim_pin(data, token_start + 1, used_pins)) {
                return false;
            }
            indicator_count++;
        } else {
            return false;
        }

        token_index++;
        token_start = cursor + 1;
    }

    return token_index >= 2 && network_count == 1 && switch_count == 3 &&
           relay_count == 3 && indicator_count == 3 && momentary_count == 1;
}

#endif

bool device_config_is_valid(const uint8_t *data, uint16_t size) {
    if (!config_structurally_valid(data, size) ||
        !device_config_resources_are_safe(data, size)) {
        return false;
    }

#ifdef DEVICE_CONFIG_GUARD_BSEED_TS0726_3GANG
    return bseed_ts0726_3gang_config_is_valid(data, size);
#else
    return true;
#endif
}

bool device_config_replace_verified(const uint8_t *data, uint16_t size) {
    if (!device_config_is_valid(data, size)) {
        printf("Rejecting invalid/unsafe device config candidate\r\n");
        return false;
    }

    device_config_str_t desired;
    memset(&desired, 0, sizeof(desired));
    desired.size = size;
    memcpy(desired.data, data, size);

    if (hal_nvm_write(NV_ITEM_DEVICE_CONFIG, sizeof(desired),
                      (uint8_t *)&desired) != HAL_NVM_SUCCESS) {
        printf("Failed to write replacement device config\r\n");
        return false;
    }

    device_config_str_t readback;
    if (hal_nvm_read(NV_ITEM_DEVICE_CONFIG, sizeof(readback),
                     (uint8_t *)&readback) != HAL_NVM_SUCCESS ||
        memcmp(&readback, &desired, sizeof(desired)) != 0) {
        printf("Replacement device config verification failed\r\n");
        return false;
    }

    memcpy(&device_config_str, &desired, sizeof(desired));
    return true;
}

void device_config_remove_from_nv() {
    hal_nvm_delete(NV_ITEM_DEVICE_CONFIG);
}
