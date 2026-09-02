#include "config_nv.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"
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
    return separators >= 2;
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
