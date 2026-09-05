#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"

#include <stdbool.h>

#ifdef HAL_SILABS
#include "silabs_config.h"
#endif

#define UNKNOWN_VERSION    0

uint16_t read_version_in_nv() {
    uint16_t version;

    hal_nvm_status_t res = hal_nvm_read(NV_ITEM_CURRENT_VERSION_IN_NV,
                                        sizeof(version), (uint8_t *)&version);

    if (res == HAL_NVM_SUCCESS) {
        printf("read version form new location\r\n");
        return version;
    }

    return UNKNOWN_VERSION;
}

void write_version_to_nv(uint16_t version) {
    hal_nvm_status_t res = hal_nvm_write(NV_ITEM_CURRENT_VERSION_IN_NV,
                                         sizeof(version), (uint8_t *)&version);

    if (res != HAL_NVM_SUCCESS) {
        printf("Failed to write lastSeenVersion to NV, st: %d\r\n", res);
    }
}

/*
 * Keep migration execution behind an explicit success result. A future
 * migration that cannot complete must return false so the stored schema
 * version is left untouched and the migration is retried on the next boot.
 */
static bool run_pending_migrations(uint16_t oldVersion) {
    (void)oldVersion;

    // Handle migrations here
    // Example:
    // if (oldVersion < XX && !migrate_to_vXX()) {
    //     return false;
    // }

    return true;
}

void handle_version_changes() {
    uint16_t oldVersion     = read_version_in_nv();
    uint16_t currentVersion = NVM_MIGRATIONS_VERSION;

    printf("Old version: %d\r\n", oldVersion);
    printf("Current version: %d\r\n", currentVersion);

    if (oldVersion == currentVersion) {
        // Same version, nothing to do.
        return;
    }

    if (oldVersion == UNKNOWN_VERSION) {
        // Either an old device or the first boot after re-flash. There is no
        // known schema to migrate from, so establish the current baseline.
        write_version_to_nv(currentVersion);
        return;
    }

    if (oldVersion > currentVersion) {
        // Never rewrite a schema marker created by newer firmware. Downgrades
        // need an explicit, separately reviewed reverse-migration path; simply
        // pretending newer NVM is older can make subsequent upgrades unsafe.
        printf("NVM schema %d is newer than firmware schema %d; refusing "
               "schema downgrade\r\n",
               oldVersion, currentVersion);
        return;
    }

    if (!run_pending_migrations(oldVersion)) {
        printf("NVM migration failed; keeping schema version %d\r\n", oldVersion);
        return;
    }

    // This must be the final step after every successful migration. Without
    // it, a device that takes a migration path re-runs that path every boot.
    write_version_to_nv(currentVersion);
}
