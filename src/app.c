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
#include "hal/zigbee.h"
#include "hal/zigbee_ota.h"
#include "zigbee/battery_cluster.h"
#include "zigbee/general_commands.h"
#ifdef END_DEVICE
#include "zigbee/poll_control_cluster.h"
#endif

void process_device_type_change() {
    enum device_type_t stored_device_type;
    hal_nvm_status_t st =
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
        hal_factory_reset();
        schedule_reboot(2000);
    }
}

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

    device_config_enable_parser_preflight();
    parse_config();
    hal_zigbee_init_ota();
    init_global_attr_write_callback();

    process_device_type_change();
}

static bool boot_announce_sent = false;

void app_task() {
    /* Meter sampling/protection/persistence is intentionally independent of
     * network join state. */
    energy_monitoring_tick();

#ifdef END_DEVICE
    poll_control_cluster_update();
#endif

    if (hal_zigbee_get_network_status() != HAL_ZIGBEE_NETWORK_JOINED &&
        hal_zigbee_get_network_status() != HAL_ZIGBEE_NETWORK_JOINING) {
        hal_zigbee_start_network_steering();
    }
    if (!boot_announce_sent &&
        hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED) {
        hal_zigbee_send_announce();
        init_energy_reporting();
        boot_announce_sent = true;
    }
}
