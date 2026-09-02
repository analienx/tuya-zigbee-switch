#include "basic_cluster.h"
#include "base_components/network_indicator.h"
#include "build_date.h"
#include "cluster_common.h"
#include "consts.h"
#include "device_config/config_nv.h"
#include "device_config/config_parser.h"
#include "device_config/device_params_nv.h"
#include "device_config/nvm_items.h"
#include "device_config/reset.h"
#include "hal/nvm.h"
#include "hal/tasks.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef HAL_SILABS
#include "silabs_config.h"
#endif

const uint8_t zclVersion   = 0x03;
const uint8_t appVersion   = 0x03;
const uint8_t stackVersion = 0x02;
const uint8_t hwVersion    = 0x00;

// Power source - set at runtime based on battery config
uint8_t powerSource = POWER_SOURCE_MAINS_1_PHASE; // 0x01 default

const uint16_t cluster_revision = 0x01;
DEF_STR(STRINGIFY_VALUE(VERSION_STR), swBuildId);
extern network_indicator_t network_indicator;

void basic_cluster_store_attrs_to_nv();
void basic_cluster_load_attrs_from_nv();

#define DEVICE_CONFIG_STAGE_CHUNK_MAX    24

typedef struct {
    bool    active;
    uint8_t transaction;
    uint8_t data[sizeof(device_config_str.data)];
    uint8_t received[sizeof(device_config_str.data)];
} device_config_stage_t;

static device_config_stage_t device_config_stage;

static void device_config_stage_reset(void) {
    memset(&device_config_stage, 0, sizeof(device_config_stage));
}

static uint16_t device_config_crc16(const uint8_t *data, uint16_t size) {
    uint16_t crc = 0xFFFF;

    for (uint16_t i = 0; i < size; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t bit = 0; bit < 8; bit++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                 : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static hal_zigbee_cmd_result_t device_config_stage_chunk(
    const uint8_t *payload, uint16_t payload_len) {
    if (payload == NULL || payload_len < 3) {
        return HAL_ZIGBEE_MALFORMED_COMMAND;
    }

    const uint8_t transaction = payload[0];
    const uint8_t offset      = payload[1];
    const uint8_t chunk_len   = payload[2];

    if (chunk_len == 0 || chunk_len > DEVICE_CONFIG_STAGE_CHUNK_MAX ||
        payload_len != (uint16_t)(3 + chunk_len) ||
        (uint16_t)offset + chunk_len >= sizeof(device_config_stage.data)) {
        return HAL_ZIGBEE_INVALID_VALUE;
    }

    if (!device_config_stage.active ||
        device_config_stage.transaction != transaction) {
        // A new transaction may only begin at offset zero. This prevents a
        // stray/replayed non-zero chunk from splicing into unrelated bytes.
        if (offset != 0) {
            return HAL_ZIGBEE_ACTION_DENIED;
        }
        device_config_stage_reset();
        device_config_stage.active      = true;
        device_config_stage.transaction = transaction;
    }

    memcpy(&device_config_stage.data[offset], &payload[3], chunk_len);
    memset(&device_config_stage.received[offset], 1, chunk_len);
    return HAL_ZIGBEE_CMD_PROCESSED;
}

static hal_zigbee_cmd_result_t device_config_commit(
    const uint8_t *payload, uint16_t payload_len) {
    if (payload == NULL || payload_len != 4) {
        return HAL_ZIGBEE_MALFORMED_COMMAND;
    }

    const uint8_t  transaction  = payload[0];
    const uint8_t  total_len    = payload[1];
    const uint16_t expected_crc =
        (uint16_t)payload[2] | ((uint16_t)payload[3] << 8);

    if (!device_config_stage.active ||
        transaction != device_config_stage.transaction) {
        return HAL_ZIGBEE_ACTION_DENIED;
    }
    if (total_len == 0 ||
        total_len >= sizeof(device_config_stage.data)) {
        device_config_stage_reset();
        return HAL_ZIGBEE_INVALID_VALUE;
    }

    for (uint16_t i = 0; i < total_len; i++) {
        if (!device_config_stage.received[i]) {
            device_config_stage_reset();
            return HAL_ZIGBEE_ACTION_DENIED;
        }
    }

    if (device_config_crc16(device_config_stage.data, total_len) != expected_crc ||
        !device_config_is_valid(device_config_stage.data, total_len)) {
        device_config_stage_reset();
        return HAL_ZIGBEE_INVALID_VALUE;
    }

    if (!device_config_replace_verified(device_config_stage.data, total_len)) {
        device_config_stage_reset();
        return HAL_ZIGBEE_ACTION_DENIED;
    }

    device_config_stage_reset();
    schedule_reboot(0);
    return HAL_ZIGBEE_CMD_PROCESSED;
}

static hal_zigbee_cmd_result_t basic_cluster_callback(
    uint8_t command_id, void *cmd_payload, uint16_t cmd_payload_len) {
    switch (command_id) {
    case ZCL_CMD_BASIC_DEVICE_CONFIG_STAGE:
        return device_config_stage_chunk((const uint8_t *)cmd_payload,
                                         cmd_payload_len);

    case ZCL_CMD_BASIC_DEVICE_CONFIG_COMMIT:
        return device_config_commit((const uint8_t *)cmd_payload,
                                    cmd_payload_len);

    default:
        return HAL_ZIGBEE_CMD_SKIPPED;
    }
}

hal_zigbee_cmd_result_t basic_cluster_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len) {
    (void)endpoint;
    (void)cluster_id;
    return basic_cluster_callback(command_id, cmd_payload, cmd_payload_len);
}

void basic_cluster_callback_attr_write_trampoline(uint16_t attribute_id) {
    basic_cluster_store_attrs_to_nv();
    if (attribute_id == ZCL_ATTR_BASIC_DEVICE_CONFIG) {
        // Preserve the legacy writable attribute ABI for tools/debugging, but
        // never persist malformed or truncated input. Ordinary Z2M uses the
        // chunked commands above to avoid APS MESSAGE_TOO_LONG failures.
        if (!device_config_is_valid(device_config_str.data,
                                    device_config_str.size)) {
            printf("Rejected invalid direct device_config write (%d)\r\n", 0);
            device_config_read_from_nv();
            return;
        }
        device_config_str.data[device_config_str.size] = 0;
        device_config_write_to_nv();
        schedule_reboot(0);
    }
    if (attribute_id == ZCL_ATTR_BASIC_STATUS_LED_STATE) {
        network_indicator_from_manual_state(&network_indicator);
    }
    if (attribute_id == ZCL_ATTR_BASIC_MULTI_PRESS_RESET_COUNT) {
        device_params_set_multi_press_reset_count(g_multi_press_reset_count);
    }
}

void basic_cluster_add_to_endpoint(zigbee_basic_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint) {
    // Set power source based on runtime battery configuration
    if (battery.pin != HAL_INVALID_PIN) {
        powerSource = POWER_SOURCE_BATTERY;
    }

    // Initialize build date buffer
    zb_build_date_init(ZB_BUILD_DATE_YYYYMMDD);

    // Fill Attrs

    SETUP_ATTR(0, ZCL_ATTR_BASIC_ZCL_VER, ZCL_DATA_TYPE_UINT8, ATTR_READONLY,
               zclVersion);

    SETUP_ATTR(1, ZCL_ATTR_BASIC_APP_VER, ZCL_DATA_TYPE_UINT8, ATTR_READONLY,
               appVersion);
    SETUP_ATTR(2, ZCL_ATTR_BASIC_STACK_VER, ZCL_DATA_TYPE_UINT8, ATTR_READONLY,
               stackVersion);
    SETUP_ATTR(3, ZCL_ATTR_BASIC_HW_VER, ZCL_DATA_TYPE_UINT8, ATTR_READONLY,
               hwVersion);
    SETUP_ATTR(4, ZCL_ATTR_BASIC_MFR_NAME, ZCL_DATA_TYPE_CHAR_STR, ATTR_READONLY,
               cluster->manuName);
    SETUP_ATTR(5, ZCL_ATTR_BASIC_MODEL_ID, ZCL_DATA_TYPE_CHAR_STR, ATTR_READONLY,
               cluster->modelId);
    SETUP_ATTR(6, ZCL_ATTR_BASIC_POWER_SOURCE, ZCL_DATA_TYPE_ENUM8, ATTR_READONLY,
               powerSource);
    SETUP_ATTR(7, ZCL_ATTR_BASIC_DEV_ENABLED, ZCL_DATA_TYPE_BOOLEAN,
               ATTR_WRITABLE, cluster->deviceEnable);
    SETUP_ATTR(8, ZCL_ATTR_BASIC_SW_BUILD_ID, ZCL_DATA_TYPE_CHAR_STR,
               ATTR_READONLY, swBuildId);
    SETUP_ATTR(9, ZCL_ATTR_BASIC_DATE_CODE, ZCL_DATA_TYPE_CHAR_STR, ATTR_READONLY,
               ZB_BUILD_DATE_YYYYMMDD);
    SETUP_ATTR(10, ZCL_ATTR_GLOBAL_CLUSTER_REVISION, ZCL_DATA_TYPE_UINT16,
               ATTR_READONLY, cluster_revision);
    SETUP_ATTR(11, ZCL_ATTR_BASIC_DEVICE_CONFIG, ZCL_DATA_TYPE_LONG_CHAR_STR,
               ATTR_WRITABLE, device_config_str);
    SETUP_ATTR(12, ZCL_ATTR_BASIC_MULTI_PRESS_RESET_COUNT, ZCL_DATA_TYPE_UINT8,
               ATTR_WRITABLE, g_multi_press_reset_count);
    if (network_indicator.has_dedicated_led) {
        SETUP_ATTR(13, ZCL_ATTR_BASIC_STATUS_LED_STATE, ZCL_DATA_TYPE_BOOLEAN,
                   ATTR_WRITABLE, network_indicator.manual_state_when_connected);
    }

    endpoint->clusters[endpoint->cluster_count].cluster_id      = ZCL_CLUSTER_BASIC;
    endpoint->clusters[endpoint->cluster_count].attribute_count =
        network_indicator.has_dedicated_led ? 14 : 13;
    endpoint->clusters[endpoint->cluster_count].attributes   = cluster->attr_infos;
    endpoint->clusters[endpoint->cluster_count].is_server    = 1;
    endpoint->clusters[endpoint->cluster_count].cmd_callback =
        basic_cluster_callback_trampoline;
    endpoint->cluster_count++;

    device_params_load_from_nv();
    basic_cluster_load_attrs_from_nv();
    if (hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED &&
        network_indicator.has_dedicated_led) {
        network_indicator_from_manual_state(&network_indicator);
    }
}

typedef struct {
    uint8_t network_led_on;
} zigbee_basic_cluster_config;

static zigbee_basic_cluster_config nv_config_buffer;

void basic_cluster_store_attrs_to_nv() {
    nv_config_buffer.network_led_on =
        network_indicator.manual_state_when_connected;

    hal_nvm_write(NV_ITEM_BASIC_CLUSTER_DATA, sizeof(zigbee_basic_cluster_config),
                  (uint8_t *)&nv_config_buffer);
}

void basic_cluster_load_attrs_from_nv() {
    hal_nvm_status_t st = hal_nvm_read(NV_ITEM_BASIC_CLUSTER_DATA,
                                       sizeof(zigbee_basic_cluster_config),
                                       (uint8_t *)&nv_config_buffer);

    if (st != HAL_NVM_SUCCESS) {
        return;
    }
    network_indicator.manual_state_when_connected =
        nv_config_buffer.network_led_on;
}
