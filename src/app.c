#include "device_config/config_nv.h"
#include "device_config/config_parser.h"
#include "device_config/device_migration.h"
#include "device_config/device_type.h"
#include "device_config/nvm_items.h"
#include "device_config/pm_legacy_migration.h"
#include "device_config/reset.h"
#include "hal/nvm.h"
#include "hal/printf_selector.h"
#include "hal/system.h"
#include "hal/timer.h"
#include "hal/zigbee.h"
#include "hal/zigbee_ota.h"
#include "zigbee/battery_cluster.h"
#include "zigbee/general_commands.h"
#include "zigbee/metering_cluster.h"
#ifdef END_DEVICE
#include "zigbee/poll_control_cluster.h"
#endif
#ifdef BSEED_MAINS_CLIENT
#include "zigbee/consts.h"
#include "zigbee/switch_cluster.h"
#endif

#ifdef BSEED_MAINS_CLIENT
extern zigbee_switch_cluster switch_clusters[];
extern uint8_t switch_clusters_cnt;

static void apply_mains_client_defaults(void) {
    zigbee_switch_cluster_config stored_config;

    for (uint8_t i = 0; i < switch_clusters_cnt; i++) {
        hal_nvm_status_t st = hal_nvm_read(
            NV_ITEM_SWITCH_CLUSTER_DATA(i), sizeof(stored_config),
            (uint8_t *)&stored_config);
        if (st == HAL_NVM_SUCCESS) {
            continue;
        }

        // Upstream testing shows release/SHORT binding can miss very fast
        // presses. For a fresh mains-client config prefer press-start/RISE;
        // existing persisted user choices still win unchanged.
        switch_clusters[i].binded_mode =
            ZCL_ONOFF_CONFIGURATION_BINDED_MODE_RISE;
        printf("Mains client: switch %d default binding mode -> RISE\r\n", i);
    }
}

#endif

#ifdef BSEED_MAINS_CLIENT
static bool process_device_type_change(void) {
    enum device_type_t stored_device_type;
    hal_nvm_status_t   st =
        hal_nvm_read(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                     (uint8_t *)&stored_device_type);

    if (st != HAL_NVM_SUCCESS) {
        stored_device_type = CURRENT_DEVICE_TYPE;
        hal_nvm_write(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                      (uint8_t *)&stored_device_type);
        return false;
    }
    if (stored_device_type != CURRENT_DEVICE_TYPE) {
        printf("Device type change detected: %d -> %d\r\n", stored_device_type,
               CURRENT_DEVICE_TYPE);
        if (!hal_role_change_reset()) {
            printf("Role-change Zigbee reset failed; rebooting for retry\r\n");
            schedule_reboot(2000);
            return true;
        }
        stored_device_type = CURRENT_DEVICE_TYPE;
        if (hal_nvm_write(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                          (uint8_t *)&stored_device_type) != HAL_NVM_SUCCESS) {
            printf("Role-change marker write failed; rebooting for retry\r\n");
        }
        schedule_reboot(2000);
        return true;
    }
    return false;
}

#else
void process_device_type_change() {
    enum device_type_t stored_device_type;
    hal_nvm_status_t   st =
        hal_nvm_read(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                     (uint8_t *)&stored_device_type);

    if (st != HAL_NVM_SUCCESS) {
        stored_device_type = CURRENT_DEVICE_TYPE;
        hal_nvm_write(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                      (uint8_t *)&stored_device_type);
        return;
    }
    if (stored_device_type != CURRENT_DEVICE_TYPE) {
        printf("Device type change detected: %d -> %d\r\n", stored_device_type,
               CURRENT_DEVICE_TYPE);
        stored_device_type = CURRENT_DEVICE_TYPE;
        hal_nvm_write(NV_ITEM_DEVICE_TYPE, sizeof(stored_device_type),
                      (uint8_t *)&stored_device_type);
        schedule_network_reset(1);
    }
}

#endif

void app_init(void) {
    handle_version_changes();

    /* The historical BSEED PM fork used NVM 40/44/51 for accepted runtime
     * metering state. Unified V8 reserves 40..50 for dimmer state, so the PM
     * target copies those legacy records into its new 64+ namespace before any
     * parser or device-specific migration can interpret the old slots. */
    if (!migrate_legacy_bseed_pm_nvm()) {
        printf("PM NVM migration: blocking init, scheduling recovery reboot\r\n");
        schedule_reboot(DEFAULT_RESET_DELAY_MS);
        return;
    }

    if (handle_device_specific_migrations() == DEVICE_MIGRATION_BLOCK_INIT) {
        printf("Device migration: blocking init, scheduling recovery "
               "reboot\r\n");
        schedule_reboot(DEFAULT_RESET_DELAY_MS);
        return;
    }

#ifdef BSEED_MAINS_CLIENT
    if (process_device_type_change()) {
        return;
    }
#endif

    device_config_enable_parser_preflight();
    parse_config();
#ifdef BSEED_MAINS_CLIENT
    apply_mains_client_defaults();
#endif
    hal_zigbee_init_ota();
    init_global_attr_write_callback();

#ifndef BSEED_MAINS_CLIENT
    process_device_type_change();
#endif
}

/* Device announces are per join epoch, not per boot. Failed local enqueue
 * must not be mistaken for successful delivery. Both Telink and Silicon
 * Labs use the same application logic for this recovery boundary. */
#define ANNOUNCE_RETRY_MS    5000u
static bool     boot_announce_sent;
static bool     announce_retry_pending;
static uint32_t last_announce_attempt_ms;

void app_rearm_network_announcement(void) {
    boot_announce_sent     = false;
    announce_retry_pending = false;
}

bool app_prepare_reboot(void) {
    energy_monitoring_tick();
    return metering_cluster_checkpoint();
}

void app_task() {
    /* Meter sampling/protection/persistence is intentionally independent of
     * network join state. */
    energy_monitoring_tick();

#ifdef END_DEVICE
    poll_control_cluster_update();
#endif

    hal_zigbee_network_status_t network_status = hal_zigbee_get_network_status();
    if (network_status == HAL_ZIGBEE_NETWORK_NOT_JOINED) {
        hal_zigbee_start_network_steering();
    }

    /* A Router or Client can regain network membership without rebooting.
     * Re-announce once after the observed loss; only retry a rejected local
     * transmit at a bounded rate. No NVM, role or relay state is changed. */
    if (network_status != HAL_ZIGBEE_NETWORK_JOINED) {
        boot_announce_sent     = false;
        announce_retry_pending = false;
    } else if (!boot_announce_sent &&
               (!announce_retry_pending ||
                (uint32_t)(hal_millis() - last_announce_attempt_ms) >=
                ANNOUNCE_RETRY_MS)) {
        last_announce_attempt_ms = hal_millis();
        announce_retry_pending   = true;
        if (hal_zigbee_send_announce() == HAL_ZIGBEE_OK) {
            init_energy_reporting();
            boot_announce_sent     = true;
            announce_retry_pending = false;
        }
    }
}
