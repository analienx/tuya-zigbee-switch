#include "device_migration.h"

#include "config_nv.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"
#include "zigbee/consts.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#ifndef STRINGIFY
#define _STRINGIFY(x)    #x
#define STRINGIFY(x)     _STRINGIFY(x)
#endif

// The build passes the swapped and canonical config strings as bare
// -D token sequences (the recipe shell strips the quotes, exactly as for
// DEFAULT_CONFIG), so stringize them into real C string literals here.
#define DEVICE_MIGRATION_FROM_CONFIG_STR    STRINGIFY(DEVICE_MIGRATION_FROM_CONFIG)
#define DEVICE_MIGRATION_TO_CONFIG_STR      STRINGIFY(DEVICE_MIGRATION_TO_CONFIG)

// Magic persisted in NV_ITEM_MIGRATION_MARKER once the migration has been
// applied, so it runs exactly once even if the device config is later edited
// back to the swapped form by hand.
#define DEVICE_MIGRATION_MARKER_MAGIC    0x42534d31UL // "BSM1"

// Zero-based relay indexes (config-string order) whose mains contact and
// panel LED are swapped on this hardware: LEFT and MIDDLE.
#define DEVICE_MIGRATION_PRESEED_RELAY_COUNT    2

static bool migration_marker_matches(void) {
    uint32_t marker = 0;

    if (hal_nvm_read(NV_ITEM_MIGRATION_MARKER, sizeof(marker),
                     (uint8_t *)&marker) != HAL_NVM_SUCCESS) {
        return false;
    }

    return marker == DEVICE_MIGRATION_MARKER_MAGIC;
}

static void write_device_config(const char *config) {
    size_t len = strlen(config);

    if (len >= sizeof(device_config_str.data)) {
        printf("Device migration: replacement config too long (%d), aborting\r\n",
               (int)len);
        return;
    }

    memset(device_config_str.data, 0, sizeof(device_config_str.data));
    memcpy(device_config_str.data, config, len);
    device_config_str.size = (uint16_t)len;
    device_config_write_to_nv();
}

#if defined(DEVICE_MIGRATION_FROM_CONFIG) && !defined(DEVICE_MIGRATION_REVERT)
static void migrate_swapped_pins_to_canonical(void) {
    if (migration_marker_matches()) {
        // One-shot: already applied, never run again.
        return;
    }

    device_config_read_from_nv();

    if (strcmp((const char *)device_config_str.data,
               DEVICE_MIGRATION_FROM_CONFIG_STR) != 0) {
        printf("Device migration: stored config is not the known swapped "
               "config, skipping\r\n");
        return;
    }

    printf("Device migration: swapped-pin config detected, migrating\r\n");
    write_device_config(DEVICE_MIGRATION_TO_CONFIG_STR);

    // Pre-seed the swapped relays to detached_on so the mains contact stays
    // energised while the panel LED takes over the old relay role. A mode
    // already stored explicitly is never clobbered.
    for (uint8_t relay_idx = 0; relay_idx < DEVICE_MIGRATION_PRESEED_RELAY_COUNT;
         relay_idx++) {
        uint8_t mode = ZCL_ONOFF_PHYSICAL_RELAY_MODE_ATTACHED;

        if (hal_nvm_read(NV_ITEM_RELAY_PHYSICAL_MODE(relay_idx), sizeof(mode),
                         &mode) == HAL_NVM_SUCCESS) {
            continue;
        }

        mode = ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON;
        if (hal_nvm_write(NV_ITEM_RELAY_PHYSICAL_MODE(relay_idx), sizeof(mode),
                          &mode) != HAL_NVM_SUCCESS) {
            printf("Device migration: failed to pre-seed relay %d physical "
                   "mode\r\n",
                   relay_idx);
        }
    }

    // Marker written last: a crash before this point re-runs the migration on
    // the next boot, which is safe because the exact-string predicate above no
    // longer matches the canonical config.
    uint32_t marker = DEVICE_MIGRATION_MARKER_MAGIC;
    if (hal_nvm_write(NV_ITEM_MIGRATION_MARKER, sizeof(marker),
                      (uint8_t *)&marker) != HAL_NVM_SUCCESS) {
        printf("Device migration: failed to persist migration marker\r\n");
    }
}

#endif // DEVICE_MIGRATION_FROM_CONFIG && !DEVICE_MIGRATION_REVERT

#ifdef DEVICE_MIGRATION_REVERT
static void revert_swapped_pins_migration(void) {
    if (!migration_marker_matches()) {
        // Nothing was migrated (or it was already reverted).
        return;
    }

    printf("Device migration revert: marker found, reverting\r\n");
    device_config_read_from_nv();

    if (strcmp((const char *)device_config_str.data,
               DEVICE_MIGRATION_TO_CONFIG_STR) == 0) {
        write_device_config(DEVICE_MIGRATION_FROM_CONFIG_STR);
    } else {
        printf("Device migration revert: stored config is not the canonical "
               "migration target, leaving it untouched\r\n");
    }

    for (uint8_t relay_idx = 0; relay_idx < DEVICE_MIGRATION_PRESEED_RELAY_COUNT;
         relay_idx++) {
        hal_nvm_delete(NV_ITEM_RELAY_PHYSICAL_MODE(relay_idx));
    }

    hal_nvm_delete(NV_ITEM_MIGRATION_MARKER);
}

#endif // DEVICE_MIGRATION_REVERT

void handle_device_specific_migrations(void) {
#if defined(DEVICE_MIGRATION_REVERT)
    revert_swapped_pins_migration();
#elif defined(DEVICE_MIGRATION_FROM_CONFIG)
    migrate_swapped_pins_to_canonical();
#else
    // No device-specific migration compiled into this build.
#endif
}
