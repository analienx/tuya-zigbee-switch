#pragma pack(push, 1)
#include "tl_common.h"
#include "zcl_include.h"
#include "ota.h"
#pragma pack(pop)

#include "telink_size_t_hack.h"

#include "hal/tasks.h"
#include "hal/zigbee_ota.h"
#include "telink_zigbee_hal.h"
#include "version_cfg.h"

// Forward declarations

void ota_process_msg_callback(u8 evt, u8 status);

#ifdef BSEED_MAINS_CLIENT
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
#ifdef BSEED_MAINS_CLIENT
    if (evt == OTA_EVT_START) {
        hal_tasks_unschedule(&ota_abort_query_retry_task);
        return;
    }
#endif

    if (evt == OTA_EVT_COMPLETE) {
        if (status == ZCL_STA_SUCCESS) {
            ota_mcuReboot();
        } else {
#ifdef BSEED_MAINS_CLIENT
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
#ifdef BSEED_MAINS_CLIENT
    hal_tasks_init(&ota_abort_query_retry_task);
    ota_abort_query_retry_task.handler = ota_abort_query_retry;
    ota_abort_query_retry_task.arg     = NULL;
#endif
    // This registers OTA cluster in ZCL and does all SDK-internal setup
    ota_init(OTA_TYPE_CLIENT, telink_zigbee_hal_zcl_get_descriptors(),
             &ota_preamble, &ota_callback);
}

void hal_zigbee_set_image_type(uint16_t image_type) {
    ota_preamble.imageType = image_type;
}
