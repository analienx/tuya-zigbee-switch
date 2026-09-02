#include "config_nv.h"
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

device_config_str_t device_config_str;

void device_config_write_to_nv() {
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

void device_config_read_from_nv() {
    hal_nvm_status_t st = 0;

    st = hal_nvm_read(NV_ITEM_DEVICE_CONFIG, sizeof(device_config_str),
                      (uint8_t *)&device_config_str);

    if (st != HAL_NVM_SUCCESS) {
        printf("Failed to read NV_ITEM_DEVICE_CONFIG, using default config "
               "instead, status: %d. (bytes: %d)\r\n",
               st, device_config_str.size);
        memcpy(device_config_str.data, default_config_data,
               sizeof(default_config_data));
        device_config_str.size = strlen((const char *)default_config_data);
    }

    printf("Using config: %d chars from\r\n%s\r\n", device_config_str.size,
           device_config_str.data);
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
            // Valid Romasku option: global debounce duration.
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
    // data[128] also needs one byte available for the parser's temporary NUL.
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
            return false; // printable ASCII only; rejects NUL/truncated data
        }
        if (ch == ';') {
            if (i == 0 || previous_was_separator) {
                return false; // empty token would terminate parse_config early
            }
            separators++;
            previous_was_separator = true;
        } else {
            previous_was_separator = false;
        }
    }

    // Manufacturer and model are the first two mandatory tokens.
    if (separators < 2) {
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
        return false;
    }

    device_config_str_t desired;
    memset(&desired, 0, sizeof(desired));
    desired.size = size;
    memcpy(desired.data, data, size);

    if (hal_nvm_write(NV_ITEM_DEVICE_CONFIG, sizeof(desired),
                      (uint8_t *)&desired) != HAL_NVM_SUCCESS) {
        return false;
    }

    device_config_str_t readback;
    if (hal_nvm_read(NV_ITEM_DEVICE_CONFIG, sizeof(readback),
                     (uint8_t *)&readback) != HAL_NVM_SUCCESS) {
        return false;
    }
    if (memcmp(&readback, &desired, sizeof(desired)) != 0) {
        return false;
    }

    memcpy(&device_config_str, &desired, sizeof(desired));
    return true;
}
