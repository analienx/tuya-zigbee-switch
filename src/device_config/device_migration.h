#ifndef DEVICE_CONFIG_DEVICE_MIGRATION_H_
#define DEVICE_CONFIG_DEVICE_MIGRATION_H_

#include <stdint.h>

// Explicit result of the device-specific migration. parse_config() may only
// run when the pin-map / NVM combination has been proven safe.
typedef enum {
    DEVICE_MIGRATION_NOT_APPLICABLE = 0, // no migration compiled into this build
    DEVICE_MIGRATION_SAFE_TO_CONTINUE,   // transaction complete or nothing to do
    DEVICE_MIGRATION_SAFE_PARTIAL,       // verified-safe partial state; parse allowed
    DEVICE_MIGRATION_BLOCK_INIT,         // unproven safety; parse_config must NOT run
} device_migration_result_t;

// Runs build-time-configured, device-specific one-shot migrations. Must be
// called before parse_config() so both the stored device config and the
// relay-cluster NVM the migration touches are still in their raw form.
//
// The migration is gated by an exact stored-config predicate and a persisted
// multi-state marker, never by DEFAULT_CONFIG: a valid stored config always
// outranks the compiled-in default. A corrupt/unknown marker fails closed.
device_migration_result_t handle_device_specific_migrations(void);

#endif /* DEVICE_CONFIG_DEVICE_MIGRATION_H_ */
