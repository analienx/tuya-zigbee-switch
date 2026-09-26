#pragma pack(push, 1)
#include "tl_common.h"
#include "zcl_include.h"
#include "ota.h"
#pragma pack(pop)

#include "telink_size_t_hack.h"

#include "hal/tasks.h"
#include "hal/zigbee.h"
#include "hal/zigbee_ota.h"
#include "telink_zigbee_hal.h"
#include "version_cfg.h"

// Forward declarations

void ota_process_msg_callback(u8 evt, u8 status);

#ifdef BSEED_PM_B28WRPVX
#define OTA_JOIN_QUERY_START_DELAY_MS    1000
static hal_task_t ota_join_query_start_task;
static bool       ota_client_initialized = false;
static bool       ota_query_requested    = false;

static void ota_join_query_start(void *arg) {
    (void)arg;
    if (!ota_client_initialized || !ota_query_requested ||
        hal_zigbee_get_network_status() != HAL_ZIGBEE_NETWORK_JOINED) {
        return;
    }
    ota_query_requested = false;
    ota_queryStart(OTA_QUERY_INTERVAL);
}

void telink_zigbee_hal_request_ota_query(void) {
    ota_query_requested = true;
    if (ota_client_initialized) {
        hal_tasks_schedule(&ota_join_query_start_task,
                           OTA_JOIN_QUERY_START_DELAY_MS);
    }
}

#endif

#if defined(BSEED_MAINS_CLIENT) || defined(BSEED_PM_B28WRPVX)
#define BSEED_OTA_DEFERRED_REQUERY    1
#endif

#ifdef BSEED_OTA_DEFERRED_REQUERY
#define OTA_ABORT_QUERY_RETRY_DELAY_MS    1000
static hal_task_t ota_abort_query_retry_task;

static void ota_abort_query_retry(void *arg) {
    (void)arg;
    ota_queryStart(OTA_PERIODIC_QUERY_INTERVAL);
}

#endif

// ota data structs

ota_preamble_t ota_preamble = {
    .fileVer          = FILE_VERSION,
    .imageType        = IMAGE_TYPE,
    .manufacturerCode = MANUFACTURER_CODE_TELINK,
};

ota_callBack_t ota_callback = {
    ota_process_msg_callback,
};

void hal_ota_cluster_setup(hal_zigbee_cluster *cluster) {
    if (cluster == NULL) {
        return;
    }
    cluster->cluster_id = ZCL_CLUSTER_OTA;
    cluster->is_server  = 0;
    // Attrs are managed by SDK internally
}

void ota_process_msg_callback(u8 evt, u8 status) {
    if (evt == OTA_EVT_START) {
#ifdef BSEED_OTA_DEFERRED_REQUERY
        hal_tasks_unschedule(&ota_abort_query_retry_task);
#endif
#if defined(ZB_ED_ROLE)
        if (status == ZCL_STA_SUCCESS) {
            hal_zigbee_set_ota_poll_active(true);
        }
#endif
        return;
    }

#if defined(ZB_ED_ROLE)
    if (evt == OTA_EVT_IMAGE_DONE || evt == OTA_EVT_COMPLETE) {
        hal_zigbee_set_ota_poll_active(false);
    }
#endif

    if (evt == OTA_EVT_COMPLETE) {
        if (status == ZCL_STA_SUCCESS) {
            ota_mcuReboot();
        } else {
#ifdef BSEED_OTA_DEFERRED_REQUERY
            // Telink uses one shared OTA timer for block-response waits and
            // periodic queries. Restart querying from a separate application
            // timer so an abort callback cannot race the shared timer cleanup.
            hal_tasks_schedule(&ota_abort_query_retry_task,
                               OTA_ABORT_QUERY_RETRY_DELAY_MS);
#else
            ota_queryStart(OTA_PERIODIC_QUERY_INTERVAL);
#endif
        }
    }
}

void hal_zigbee_init_ota() {
#ifdef BSEED_PM_B28WRPVX
    hal_tasks_init(&ota_join_query_start_task);
    ota_join_query_start_task.handler = ota_join_query_start;
    ota_join_query_start_task.arg     = NULL;
#endif
#ifdef BSEED_OTA_DEFERRED_REQUERY
    hal_tasks_init(&ota_abort_query_retry_task);
    ota_abort_query_retry_task.handler = ota_abort_query_retry;
    ota_abort_query_retry_task.arg     = NULL;
#endif
    // This registers OTA cluster in ZCL and does all SDK-internal setup
    ota_init(OTA_TYPE_CLIENT, telink_zigbee_hal_zcl_get_descriptors(),
             &ota_preamble, &ota_callback);
#ifdef BSEED_PM_B28WRPVX
    ota_client_initialized = true;
    if (ota_query_requested ||
        hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED) {
        telink_zigbee_hal_request_ota_query();
    }
#endif
}

void hal_zigbee_set_image_type(uint16_t image_type) {
    ota_preamble.imageType = image_type;
}
