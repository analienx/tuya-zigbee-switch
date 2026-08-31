#include "device_migration.h"

#include "config_nv.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "nvm_items.h"
#include "zigbee/relay_cluster.h"
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

// Multi-state marker stored in NV_ITEM_MIGRATION_MARKER as a uint32. Item
// absence is equivalent to MIG_STATE_NONE. Every boot can therefore classify
// the NVM into exactly one of:
//   canonical + detached_on, swapped + MANUAL/ON, or "not ours (yet)".
#define MIG_STATE_NONE                   0x00000000
#define MIG_STATE_FORWARD_IN_PROGRESS    0x00000001
#define MIG_STATE_FORWARD_COMPLETE       0x00000002
#define MIG_STATE_REVERT_IN_PROGRESS     0x00000003
#define MIG_STATE_MAX_VALID              MIG_STATE_REVERT_IN_PROGRESS

// Zero-based relay indexes (config-string order) whose mains contact and
// panel LED are swapped on this hardware: LEFT and MIDDLE.
#define DEVICE_MIGRATION_SWAPPED_RELAY_COUNT    2

#if defined(DEVICE_MIGRATION_FROM_CONFIG) || defined(DEVICE_MIGRATION_REVERT)
static bool migration_marker_state(uint32_t *state) {
    *state = MIG_STATE_NONE;

    uint32_t stored = 0;
    if (hal_nvm_read(NV_ITEM_MIGRATION_MARKER, sizeof(stored),
                     (uint8_t *)&stored) != HAL_NVM_SUCCESS) {
        return false; // absent == NONE
    }

    if (stored > MIG_STATE_MAX_VALID) {
        printf("Device migration: unknown marker state %lu, treating as "
               "absent\r\n",
               (unsigned long)stored);
        return false;
    }

    *state = stored;
    return true;
}

static bool write_marker_state(uint32_t state) {
    uint32_t current = 0;

    if (hal_nvm_read(NV_ITEM_MIGRATION_MARKER, sizeof(current),
                     (uint8_t *)&current) == HAL_NVM_SUCCESS &&
        current == state) {
        return true; // already persisted
    }

    if (hal_nvm_write(NV_ITEM_MIGRATION_MARKER, sizeof(state),
                      (uint8_t *)&state) != HAL_NVM_SUCCESS) {
        return false;
    }

    uint32_t readback = 0;
    if (hal_nvm_read(NV_ITEM_MIGRATION_MARKER, sizeof(readback),
                     (uint8_t *)&readback) != HAL_NVM_SUCCESS) {
        return false;
    }

    return readback == state;
}

#ifdef DEVICE_MIGRATION_REVERT
static bool delete_migration_marker(void) {
    // Ignore the delete result: absence is the target state and is verified
    // below regardless.
    hal_nvm_delete(NV_ITEM_MIGRATION_MARKER);

    uint32_t probe = 0;
    return hal_nvm_read(NV_ITEM_MIGRATION_MARKER, sizeof(probe),
                        (uint8_t *)&probe) == HAL_NVM_NOT_FOUND;
}

#endif // DEVICE_MIGRATION_REVERT

static bool write_device_config_verified(const char *config) {
    size_t len = strlen(config);

    if (len >= sizeof(device_config_str.data)) {
        printf("Device migration: replacement config too long (%d)\r\n",
               (int)len);
        return false;
    }

    device_config_str_t desired;
    memset(&desired, 0, sizeof(desired));
    memcpy(desired.data, config, len);
    desired.size = (uint16_t)len;

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
        printf("Device migration: config readback mismatch\r\n");
        return false;
    }

    // Commit the verified bytes to the in-memory copy parse_config() uses.
    memcpy(&device_config_str, &desired, sizeof(desired));
    return true;
}

static bool ensure_swapped_relay_safety(void) {
    // MANUAL + ON keeps the indicator-driven mains side energised and
    // uncoupled under BOTH pin maps, so this phase is safe before and after
    // the config rewrite.
    for (uint8_t relay_idx = 0;
         relay_idx < DEVICE_MIGRATION_SWAPPED_RELAY_COUNT;
         relay_idx++) {
        if (!relay_cluster_nv_set_indicator_safety(relay_idx)) {
            printf("Device migration: failed to secure relay %d indicator "
                   "NVM\r\n",
                   relay_idx);
            return false;
        }
    }

    return true;
}

#if defined(DEVICE_MIGRATION_FROM_CONFIG) && !defined(DEVICE_MIGRATION_REVERT)
static bool ensure_swapped_relay_modes(void) {
    // detached_on pins the R-side contact. While the config is still swapped
    // this only pins the panel-LED side; once the canonical config exists,
    // the modes are already durable before C2/C3 become R.
    for (uint8_t relay_idx = 0;
         relay_idx < DEVICE_MIGRATION_SWAPPED_RELAY_COUNT;
         relay_idx++) {
        if (!relay_cluster_nv_ensure_physical_mode(
                relay_idx, ZCL_ONOFF_PHYSICAL_RELAY_MODE_DETACHED_ON)) {
            printf("Device migration: failed to pre-seed relay %d physical "
                   "mode\r\n",
                   relay_idx);
            return false;
        }
    }

    return true;
}

#endif // DEVICE_MIGRATION_FROM_CONFIG && !DEVICE_MIGRATION_REVERT

#endif // DEVICE_MIGRATION_FROM_CONFIG || DEVICE_MIGRATION_REVERT

#if defined(DEVICE_MIGRATION_FROM_CONFIG) && !defined(DEVICE_MIGRATION_REVERT)
static void migrate_swapped_pins_to_canonical(void) {
    uint32_t state      = MIG_STATE_NONE;
    bool     have_state = migration_marker_state(&state);

    if (have_state && state == MIG_STATE_FORWARD_COMPLETE) {
        // One-shot: already applied, never run again.
        return;
    }

    if (have_state && state == MIG_STATE_REVERT_IN_PROGRESS) {
        // A revert image owns this NVM; never forward-migrate over it.
        printf("Device migration: revert in progress, forward migration "
               "refuses to run\r\n");
        return;
    }

    device_config_read_from_nv();

    const bool is_swapped = strcmp((const char *)device_config_str.data,
                                   DEVICE_MIGRATION_FROM_CONFIG_STR) == 0;
    const bool is_canonical = strcmp((const char *)device_config_str.data,
                                     DEVICE_MIGRATION_TO_CONFIG_STR) == 0;
    const bool resuming = have_state &&
                          state == MIG_STATE_FORWARD_IN_PROGRESS;

    if (!is_swapped && !(is_canonical && resuming)) {
        if (resuming) {
            // Protected invariant failure: an interrupted migration must
            // never be silently marked complete for a foreign config.
            printf("Device migration: forward in progress but config matches "
                   "neither known form; leaving NVM untouched for manual "
                   "recovery\r\n");
        }
        return;
    }

    if (!resuming) {
        if (!write_marker_state(MIG_STATE_FORWARD_IN_PROGRESS)) {
            printf("Device migration: failed to persist "
                   "FORWARD_IN_PROGRESS\r\n");
            return;
        }
    }

    // Phase C: indicator safety.
    if (!ensure_swapped_relay_safety()) {
        return; // retry next boot
    }

    // Phase D: detached_on pre-seed.
    if (!ensure_swapped_relay_modes()) {
        return; // retry next boot
    }

    // Phase E: canonical config (skipped when resuming a config that is
    // already canonical).
    if (!is_canonical) {
        if (!write_device_config_verified(DEVICE_MIGRATION_TO_CONFIG_STR)) {
            printf("Device migration: failed to write canonical config\r\n");
            return; // retry next boot
        }
    }

    // Phase F: only now is the transaction complete.
    if (!write_marker_state(MIG_STATE_FORWARD_COMPLETE)) {
        printf("Device migration: failed to persist FORWARD_COMPLETE\r\n");
        return; // retry next boot; the boot state is already safe
    }

    printf("Device migration: swapped-pin migration complete\r\n");
}

#endif // DEVICE_MIGRATION_FROM_CONFIG && !DEVICE_MIGRATION_REVERT

#ifdef DEVICE_MIGRATION_REVERT
static void revert_swapped_pins_migration(void) {
    uint32_t state      = MIG_STATE_NONE;
    bool     have_state = migration_marker_state(&state);

    if (!have_state || state == MIG_STATE_NONE) {
        return; // never migrated (or already reverted)
    }

    // FORWARD_IN_PROGRESS may only exist if a forward migration was
    // interrupted; its partial state is safe by construction, so reverting it
    // is valid. FORWARD_COMPLETE is the normal revert source.
    if (!write_marker_state(MIG_STATE_REVERT_IN_PROGRESS)) {
        printf("Device migration revert: failed to persist "
               "REVERT_IN_PROGRESS\r\n");
        return;
    }

    // Phase 2: indicator safety FIRST. Canonical operation may have changed
    // the indicator mode (e.g. SAME); reverting the pin map before restoring
    // MANUAL + ON would move that behavior onto the C2/C3 mains relays.
    if (!ensure_swapped_relay_safety()) {
        return; // retry next boot
    }

    // Phase 3: swapped config, verified byte-exact.
    if (!write_device_config_verified(DEVICE_MIGRATION_FROM_CONFIG_STR)) {
        printf("Device migration revert: failed to restore swapped config\r\n");
        return; // retry next boot
    }

    // Phase 4: neutralize physical-mode slots. Absence makes the cluster use
    // its ATTACHED default, which under the swapped map drives the panel-LED
    // side again - the exact pre-migration semantics.
    for (uint8_t relay_idx = 0;
         relay_idx < DEVICE_MIGRATION_SWAPPED_RELAY_COUNT;
         relay_idx++) {
        if (!relay_cluster_nv_delete_physical_mode(relay_idx)) {
            printf("Device migration revert: failed to neutralize relay %d "
                   "physical mode\r\n",
                   relay_idx);
            return; // retry next boot
        }
    }

    // Phase 5: only now clear the migration state.
    if (!delete_migration_marker()) {
        printf("Device migration revert: failed to clear migration marker\r\n");
        return; // retry next boot; the reverted state is safe
    }

    printf("Device migration revert: swapped-pin state restored\r\n");
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
