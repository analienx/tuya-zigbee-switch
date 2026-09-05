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

static const char emergency_config_data[] = "unknown;TS0012-CUSTOM;";

device_config_str_t device_config_str;
static bool parser_preflight_enabled = false;

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

static bool config_token_equals(const uint8_t *data, uint16_t start,
                                uint16_t len, const char *expected) {
    size_t expected_len = strlen(expected);

    return len == expected_len &&
           memcmp(&data[start], expected, expected_len) == 0;
}

static bool config_structurally_valid(const uint8_t *data, uint16_t size) {
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
        // The emergency minimal config is intentionally constructed to fit;
        // this branch is defensive only and leaves parsing with zero GPIO
        // resources rather than an unsafe candidate.
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
    uint16_t token_start           = 0;
    uint16_t token_index           = 0;

    for (uint16_t cursor = 0; cursor < size; cursor++) {
        if (data[cursor] != ';') {
            continue;
        }

        uint16_t len = cursor - token_start;
        if (token_index >= 2) {
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
                // Battery token: BT<pin>, e.g. BTC5.
                if (len != 4) {
                    return false;
                }
                battery_tokens++;
            } else if (kind == 'B') {
                if (len != 4) {
                    return false;
                }
                buttons++;
            } else if (kind == 'L' || kind == 'I') {
                if ((len != 3 && len != 4) ||
                    (len == 4 && data[token_start + 3] != 'i')) {
                    return false;
                }
                leds++;
            } else if (kind == 'S') {
                if (len != 4) {
                    return false;
                }
                buttons++;
                switch_clusters++;
            } else if (kind == 'R') {
                if (len != 3 && len != 5) {
                    return false;
                }
                relays++;
                relay_clusters++;
            } else if (kind == 'X') {
                // X<open-pin><close-pin><pull>: two buttons, one endpoint.
                if (len != 6) {
                    return false;
                }
                buttons += 2;
                cover_switch_clusters++;
            } else if (kind == 'C') {
                // C<open-pin><close-pin>: two relays, one endpoint.
                if (len != 5) {
                    return false;
                }
                relays += 2;
                cover_clusters++;
            } else if (kind == 'i') {
                if (len < 2 ||
                    !config_digits_only(data, token_start + 1, len - 1)) {
                    return false;
                }
            } else {
                // Preserve generic/upstream compatibility: parse_config has
                // historically ignored unknown extension tokens. They consume
                // no fixed storage, so they are resource-safe. Board-specific
                // guards (notably BSEED) may still reject them at commit time.
            }
        }

        token_index++;
        token_start = cursor + 1;
    }

    if (battery_tokens > 1 ||
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

    // Shared cluster-pool accounting. Endpoint 1 always owns Basic + OTA.
    // A switch adds four clusters; a relay adds OnOff + Level + Groups; a
    // cover switch adds three; a cover adds one. Battery and PollControl are
    // optional additions to endpoint 1.
    uint16_t total_clusters = 2 + switch_clusters * 4 + relay_clusters * 3 +
                              cover_switch_clusters * 3 + cover_clusters;
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

    // Transactional/fail-closed parser rule: do not let an unsafe stored
    // candidate partially initialize GPIOs or Zigbee topology. Preserve the
    // suspect NVM bytes for diagnosis/recovery and use the compiled canonical
    // board config only in RAM for this boot.
    printf("Stored device config is unsafe; using compiled default in RAM\r\n");
    load_config_copy(default_config_data);
    if (device_config_resources_are_safe(device_config_str.data,
                                         device_config_str.size)) {
        return true;
    }

    // A broken build-time DEFAULT_CONFIG must still never reach the parser.
    // The minimal emergency config consumes no GPIO/endpoint resources beyond
    // the mandatory Basic/OTA endpoint and keeps the device recoverable.
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
