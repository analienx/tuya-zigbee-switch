#include "pm_legacy_migration.h"

#include "config_nv.h"
#include "nvm_items.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "base_components/overload_protection.h"

#include <stdint.h>
#include <string.h>

#ifdef BSEED_PM_B28WRPVX
#define LEGACY_PM_ENERGY_EP1         40
#define LEGACY_PM_CALIBRATION        44
#define LEGACY_PM_OVERLOAD_CONFIG    51
#define PM_IDENTITY_PREFIX           "b28wrpvx;TS011F-BS-PM;"

typedef struct {
    uint64_t accumulated_energy_wh;
} pm_energy_nv_t;

typedef struct {
    uint32_t magic;
    uint32_t voltage_multiplier;
    uint32_t current_multiplier;
    uint32_t power_multiplier;
} pm_calibration_nv_t;

static bool copy_item_if_destination_absent(uint16_t legacy_item,
                                            uint16_t destination_item,
                                            uint16_t size,
                                            uint8_t *buffer,
                                            const char *name) {
    hal_nvm_status_t dst = hal_nvm_read(destination_item, size, buffer);

    if (dst == HAL_NVM_SUCCESS) {
        return true; // already migrated / unified state owns this item
    }
    if (dst != HAL_NVM_NOT_FOUND) {
        printf("PM NVM migration: failed reading destination %s (%d)\r\n",
               name, dst);
        return false;
    }

    hal_nvm_status_t src = hal_nvm_read(legacy_item, size, buffer);
    if (src == HAL_NVM_NOT_FOUND) {
        return true; // no historical value; compiled/default state is valid
    }
    if (src != HAL_NVM_SUCCESS) {
        printf("PM NVM migration: failed reading legacy %s (%d)\r\n",
               name, src);
        return false;
    }

    uint8_t expected[32];
    if (size > sizeof(expected)) {
        printf("PM NVM migration: %s record too large\r\n", name);
        return false;
    }
    memcpy(expected, buffer, size);

    if (hal_nvm_write(destination_item, size, expected) != HAL_NVM_SUCCESS) {
        printf("PM NVM migration: failed writing %s\r\n", name);
        return false;
    }

    memset(buffer, 0, size);
    if (hal_nvm_read(destination_item, size, buffer) != HAL_NVM_SUCCESS ||
        memcmp(buffer, expected, size) != 0) {
        printf("PM NVM migration: verification failed for %s\r\n", name);
        return false;
    }

    printf("PM NVM migration: preserved legacy %s in unified namespace\r\n",
           name);
    return true;
}

#endif

bool migrate_legacy_bseed_pm_nvm(void) {
#ifndef BSEED_PM_B28WRPVX
    return true;
#else
    /* Run before parser preflight, but classify only from raw device_config.
     * This prevents legacy item 40 (energy in the PM fork) from being confused
     * with the TS0726 migration marker, while making the migration a complete
     * no-op for every other identity. */
    device_config_read_raw_from_nv();
    const uint16_t prefix_len = (uint16_t)(sizeof(PM_IDENTITY_PREFIX) - 1u);
    if (device_config_str.size < prefix_len ||
        memcmp(device_config_str.data, PM_IDENTITY_PREFIX, prefix_len) != 0) {
        return true;
    }

    uint8_t buffer[32];
    if (!copy_item_if_destination_absent(
            LEGACY_PM_ENERGY_EP1, NV_ITEM_ENERGY_ACCUMULATION(1),
            sizeof(pm_energy_nv_t), buffer, "energy")) {
        return false;
    }
    if (!copy_item_if_destination_absent(
            LEGACY_PM_CALIBRATION, NV_ITEM_ENERGY_CALIBRATION,
            sizeof(pm_calibration_nv_t), buffer, "calibration")) {
        return false;
    }
    if (!copy_item_if_destination_absent(
            LEGACY_PM_OVERLOAD_CONFIG, NV_ITEM_OVERLOAD_CONFIG,
            sizeof(overload_config_t), buffer, "overload config")) {
        return false;
    }

    return true;
#endif
}
