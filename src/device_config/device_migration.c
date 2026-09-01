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

// Marker read classification. Corruption fails closed (BLOCK), it is never
// converted to "absent": an unknown marker must not re-arm a one-shot
// migration on a device whose history is unknown.
typedef enum {
    MARKER_ABSENT = 0,
    MARKER_VALID,
    MARKER_INVALID,
} marker_status_t;

#if defined(DEVICE_MIGRATION_FROM_CONFIG) || defined(DEVICE_MIGRATION_REVERT)
static marker_status_t migration_marker_state(uint32_t *state) {
    *state = MIG_STATE_NONE;

    uint32_t         stored = 0;
    hal_nvm_status_t st     = hal_nvm_read(NV_ITEM_MIGRATION_MARKER,
                                           sizeof(stored), (uint8_t *)&stored);
    if (st == HAL_NVM_NOT_FOUND) {
        return MARKER_ABSENT;
    }
    if (st != HAL_NVM_SUCCESS) {
        printf("Device migration: marker read failed (%lu), fail closed\r\n",
               (unsigned long)st);
        return MARKER_INVALID;
    }

    if (stored > MIG_STATE_MAX_VALID) {
        printf("Device migration: corrupt marker state %lu, fail closed\r\n",
               (unsigned long)stored);
        return MARKER_INVALID;
    }

    *state = stored;
    return MARKER_VALID;
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

static bool ensure_swapped_relay_modes(void) {
    // detached_on pins the R-side contact. While the config is still swapped
    // this only pins the panel-LED side; once the canonical config exists,
    // the modes are already durable before C2/C3 become R. The recovery
    // image uses the same phase to re-prove current-state canonical safety.
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

#endif // DEVICE_MIGRATION_FROM_CONFIG || DEVICE_MIGRATION_REVERT

#if defined(DEVICE_MIGRATION_FROM_CONFIG) && !defined(DEVICE_MIGRATION_REVERT)
static device_migration_result_t migrate_swapped_pins_to_canonical(void) {
    uint32_t        state  = MIG_STATE_NONE;
    marker_status_t marker = migration_marker_state(&state);

    if (marker == MARKER_INVALID) {
        // Fail closed: an unreadable/corrupt transaction marker must never
        // re-arm a one-shot migration or parse an unproven NVM state.
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    device_config_read_from_nv();

    const bool is_swapped = strcmp((const char *)device_config_str.data,
                                   DEVICE_MIGRATION_FROM_CONFIG_STR) == 0;
    const bool is_canonical = strcmp((const char *)device_config_str.data,
                                     DEVICE_MIGRATION_TO_CONFIG_STR) == 0;

    if (marker == MARKER_VALID && state == MIG_STATE_FORWARD_COMPLETE) {
        // Completed-state invariant: never trust historical state. On the
        // canonical map the mains side is the R contact, so every boot must
        // re-prove DETACHED_ON; a missing/wrong slot is forced back and
        // verified, and an unprovable slot blocks init.
        if (is_canonical && !ensure_swapped_relay_modes()) {
            printf("Device migration: completed state but DETACHED_ON not "
                   "provable; blocking init\r\n");
            return DEVICE_MIGRATION_BLOCK_INIT;
        }
        return DEVICE_MIGRATION_SAFE_TO_CONTINUE;
    }

    if (marker == MARKER_VALID && state == MIG_STATE_REVERT_IN_PROGRESS) {
        // A revert image owns this NVM; never forward-migrate over it.
        printf("Device migration: revert in progress, forward migration "
               "refuses to run\r\n");
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    const bool resuming = marker == MARKER_VALID &&
                          state == MIG_STATE_FORWARD_IN_PROGRESS;

    if (!is_swapped && !(is_canonical && resuming)) {
        if (resuming) {
            // Protected invariant failure: an interrupted migration must
            // never be silently marked complete for a foreign config, and
            // its pin-map/NVM combination is unproven. Block, do not parse.
            printf("Device migration: forward in progress but config matches "
                   "neither known form; blocking init for manual recovery\r\n");
            return DEVICE_MIGRATION_BLOCK_INIT;
        }
        // Foreign/factory config without our marker: not ours to touch.
        return DEVICE_MIGRATION_SAFE_TO_CONTINUE;
    }

    if (!resuming) {
        if (!write_marker_state(MIG_STATE_FORWARD_IN_PROGRESS)) {
            printf("Device migration: failed to persist "
                   "FORWARD_IN_PROGRESS\r\n");
            // Swapped config with unproven indicator NVM: not parseable.
            return DEVICE_MIGRATION_BLOCK_INIT;
        }
    }

    // Phase C: indicator safety. Until this is verified, the swapped map may
    // still be active with an unproven mains side - never parse on failure.
    if (!ensure_swapped_relay_safety()) {
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    // Phase D: detached_on pre-seed (forced to the exact mode and verified).
    if (!ensure_swapped_relay_modes()) {
        if (is_canonical) {
            // Canonical map: C2/C3 are R and DETACHED_ON is not proven.
            // Parsing would expose mains to ATTACHED/DETACHED_OFF semantics.
            printf("Device migration: canonical config with unprovable "
                   "DETACHED_ON; blocking init\r\n");
            return DEVICE_MIGRATION_BLOCK_INIT;
        }
        // Swapped config + verified MANUAL/ON: explicitly safe partial.
        return DEVICE_MIGRATION_SAFE_PARTIAL;
    }

    // Phase E: canonical config (skipped when resuming a config that is
    // already canonical).
    if (!is_canonical) {
        if (!write_device_config_verified(DEVICE_MIGRATION_TO_CONFIG_STR)) {
            // Swapped + MANUAL/ON + verified DETACHED_ON: safe partial.
            return DEVICE_MIGRATION_SAFE_PARTIAL;
        }
    }

    // Phase F: only now is the transaction complete.
    if (!write_marker_state(MIG_STATE_FORWARD_COMPLETE)) {
        // Canonical + verified DETACHED_ON: safe partial.
        return DEVICE_MIGRATION_SAFE_PARTIAL;
    }

    printf("Device migration: swapped-pin migration complete\r\n");
    return DEVICE_MIGRATION_SAFE_TO_CONTINUE;
}

#endif // DEVICE_MIGRATION_FROM_CONFIG && !DEVICE_MIGRATION_REVERT

#ifdef DEVICE_MIGRATION_REVERT
static device_migration_result_t revert_swapped_pins_migration(void) {
    uint32_t        state  = MIG_STATE_NONE;
    marker_status_t marker = migration_marker_state(&state);

    if (marker == MARKER_INVALID) {
        // Fail closed on an unreadable/corrupt transaction marker.
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    if (marker == MARKER_ABSENT) {
        // Never migrated (or already reverted): nothing is ours to revert.
        return DEVICE_MIGRATION_SAFE_TO_CONTINUE;
    }

    // Classify BEFORE any mutation: a foreign/user-edited config under our
    // marker must be left byte-for-byte untouched (protected invariant).
    device_config_read_from_nv();

    const bool is_swapped = strcmp((const char *)device_config_str.data,
                                   DEVICE_MIGRATION_FROM_CONFIG_STR) == 0;
    const bool is_canonical = strcmp((const char *)device_config_str.data,
                                     DEVICE_MIGRATION_TO_CONFIG_STR) == 0;

    // FORWARD_COMPLETE + swapped means the pin map is already back to the
    // swapped form; only the safety cleanup is performed (no config write).
    const bool resumable = is_swapped || is_canonical;

    if (!resumable) {
        printf("Device migration revert: protected invariant failure (marker "
               "state %lu with foreign config); NVM left untouched\r\n",
               (unsigned long)state);
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    if (is_canonical) {
        // Phase 0: current canonical safety must be proven NOW. Historical
        // FORWARD_COMPLETE does not prove the modes still hold - a missing,
        // wrong or corrupted slot must be forced back to DETACHED_ON and
        // verified before anything may parse the canonical map.
        if (!ensure_swapped_relay_modes()) {
            printf("Device migration revert: cannot establish DETACHED_ON "
                   "for canonical mains; blocking init\r\n");
            return DEVICE_MIGRATION_BLOCK_INIT;
        }
    }

    if (!write_marker_state(MIG_STATE_REVERT_IN_PROGRESS)) {
        // Canonical entry: phase-0-verified DETACHED_ON guards the mains.
        // Swapped entry: the indicator side is not yet proven - never parse.
        return is_canonical ? DEVICE_MIGRATION_SAFE_PARTIAL
                            : DEVICE_MIGRATION_BLOCK_INIT;
    }

    // Phase 2: indicator safety FIRST. Canonical operation may have changed
    // the indicator mode (e.g. SAME); reverting the pin map before restoring
    // MANUAL + ON would move that behavior onto the C2/C3 mains relays.
    if (!ensure_swapped_relay_safety()) {
        if (is_canonical) {
            // Canonical map: mains is the R side, still guarded by the
            // forward's verified DETACHED_ON modes. Safe partial.
            return DEVICE_MIGRATION_SAFE_PARTIAL;
        }
        // Swapped map with unproven indicator side: never parse.
        return DEVICE_MIGRATION_BLOCK_INIT;
    }

    // Phase 3: swapped config, verified byte-exact (skipped when the config
    // is already the swapped form).
    if (!is_swapped) {
        if (!write_device_config_verified(DEVICE_MIGRATION_FROM_CONFIG_STR)) {
            // Canonical + forward-verified DETACHED_ON: safe partial.
            return DEVICE_MIGRATION_SAFE_PARTIAL;
        }
    }

    // Phase 4: neutralize physical-mode slots. Absence makes the cluster use
    // its ATTACHED default, which under the swapped map drives the panel-LED
    // side again - the exact pre-migration semantics.
    for (uint8_t relay_idx = 0;
         relay_idx < DEVICE_MIGRATION_SWAPPED_RELAY_COUNT;
         relay_idx++) {
        if (!relay_cluster_nv_delete_physical_mode(relay_idx)) {
            // Swapped + verified MANUAL/ON: safe partial.
            return DEVICE_MIGRATION_SAFE_PARTIAL;
        }
    }

    // Phase 5: only now clear the migration state.
    if (!delete_migration_marker()) {
        // Swapped + verified MANUAL/ON: safe partial.
        return DEVICE_MIGRATION_SAFE_PARTIAL;
    }

    printf("Device migration revert: swapped-pin state restored\r\n");
    return DEVICE_MIGRATION_SAFE_TO_CONTINUE;
}

#endif // DEVICE_MIGRATION_REVERT

device_migration_result_t handle_device_specific_migrations(void) {
#if defined(DEVICE_MIGRATION_REVERT)
    return revert_swapped_pins_migration();
#elif defined(DEVICE_MIGRATION_FROM_CONFIG)
    return migrate_swapped_pins_to_canonical();
#else
    // No device-specific migration compiled into this build.
    return DEVICE_MIGRATION_NOT_APPLICABLE;
#endif
}
