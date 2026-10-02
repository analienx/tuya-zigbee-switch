#include "metering_cluster.h"
#include "cluster_common.h"
#include "consts.h"
#include "device_config/nvm_items.h"
#include "hal/nvm.h"
#include "hal/timer.h"
#include "hal/printf_selector.h"
#include <string.h>

#define NVM_SAVE_INTERVAL_MS                   300000
#define NVM_RETRY_INTERVAL_MS                  30000
#define METERING_BASELINE_RETRY_INTERVAL_MS    300000
#define METERING_SUMMATION_UNTRUSTED           0xFFFFFFFFFFFFULL
#define METERING_DEVICE_TYPE_ELECTRIC          0x00
#define UNIT_OF_MEASURE_KWH                    0x00

typedef struct {
    uint64_t accumulated_energy_wh;
} metering_nv_data_t;

static metering_cluster_t *g_metering_cluster = NULL;

void metering_cluster_init(metering_cluster_t *cluster,
                           energy_meter_t *meter) {
    if (!cluster || !meter)
        return;

    memset(cluster, 0, sizeof(*cluster));
    cluster->meter                = meter;
    cluster->status               = 0x00;
    cluster->unit_of_measure      = UNIT_OF_MEASURE_KWH;
    cluster->multiplier           = 1;
    cluster->divisor              = 1000;
    cluster->summation_formatting = 0x2B;
    cluster->metering_device_type = METERING_DEVICE_TYPE_ELECTRIC;
}

void metering_cluster_add_to_endpoint(metering_cluster_t *cluster,
                                      hal_zigbee_endpoint *endpoint) {
    if (!cluster || !endpoint)
        return;

    cluster->endpoint  = endpoint->endpoint;
    g_metering_cluster = cluster;
    metering_cluster_load_energy(cluster);

    SETUP_ATTR(0, ZCL_ATTR_METERING_CURRENT_SUMMATION_DELIVERED,
               ZCL_DATA_TYPE_UINT48, ATTR_READONLY,
               cluster->current_summation_delivered);
    SETUP_ATTR(1, ZCL_ATTR_METERING_STATUS, ZCL_DATA_TYPE_BITMAP8,
               ATTR_READONLY, cluster->status);
    SETUP_ATTR(2, ZCL_ATTR_METERING_UNIT_OF_MEASURE, ZCL_DATA_TYPE_ENUM8,
               ATTR_READONLY, cluster->unit_of_measure);
    SETUP_ATTR(3, ZCL_ATTR_METERING_MULTIPLIER, ZCL_DATA_TYPE_UINT24,
               ATTR_READONLY, cluster->multiplier);
    SETUP_ATTR(4, ZCL_ATTR_METERING_DIVISOR, ZCL_DATA_TYPE_UINT24,
               ATTR_READONLY, cluster->divisor);
    SETUP_ATTR(5, ZCL_ATTR_METERING_SUMMATION_FORMATTING,
               ZCL_DATA_TYPE_BITMAP8, ATTR_READONLY,
               cluster->summation_formatting);
    SETUP_ATTR(6, ZCL_ATTR_METERING_METERING_DEVICE_TYPE,
               ZCL_DATA_TYPE_BITMAP8, ATTR_READONLY,
               cluster->metering_device_type);
    SETUP_ATTR(7, ZCL_ATTR_METERING_CUST_RESET_ENERGY,
               ZCL_DATA_TYPE_UINT8, ATTR_WRITABLE,
               cluster->reset_trigger);

    endpoint->clusters[endpoint->cluster_count].cluster_id      = ZCL_CLUSTER_METERING;
    endpoint->clusters[endpoint->cluster_count].attribute_count = 8;
    endpoint->clusters[endpoint->cluster_count].attributes      = cluster->attr_infos;
    endpoint->clusters[endpoint->cluster_count].is_server       = 1;
    endpoint->cluster_count++;
    printf("Metering: Added to endpoint %d, energy=%llu Wh\r\n",
           endpoint->endpoint,
           (unsigned long long)cluster->current_summation_delivered);
}

void metering_cluster_update(metering_cluster_t *cluster) {
    if (!cluster || !cluster->meter)
        return;

    /* A storage error is not the same as an absent record.  Do not accumulate
     * from a zero/unknown baseline or allow a later periodic save to destroy
     * the last good total.  Retry restoration before doing any meter math. */
    if (!cluster->energy_baseline_valid) {
        uint32_t now = hal_millis();
        if (now - cluster->last_baseline_retry_time >=
            METERING_BASELINE_RETRY_INTERVAL_MS) {
            metering_cluster_load_energy(cluster);
        }

        /* Restoration seeds the live counter. Resume accumulation on the next
         * tick, without writing back on the recovery tick itself. */
        return;
    }

    energy_meter_data_t data;
    memset(&data, 0, sizeof(data));
    energy_meter_get_data(cluster->meter, &data);
    if (!data.valid)
        return;

    uint32_t current_energy = data.energy;
    if (!cluster->last_energy_value_seeded) {
        /* Seed the delta base without accumulating or saving; the next tick
         * resumes normally. A restored total is never double-counted. */
        cluster->last_energy_value        = current_energy;
        cluster->last_energy_value_seeded = 1;
        return;
    }
    if (current_energy >= cluster->last_energy_value) {
        cluster->current_summation_delivered +=
            current_energy - cluster->last_energy_value;
    }
    cluster->last_energy_value = current_energy;

    uint32_t now = hal_millis();
    uint32_t interval = cluster->persistence_retry_pending ?
                        NVM_RETRY_INTERVAL_MS : NVM_SAVE_INTERVAL_MS;
    uint32_t last = cluster->persistence_retry_pending ?
                    cluster->last_nvm_attempt_time : cluster->last_nvm_save_time;
    if ((cluster->persistence_retry_pending ||
         cluster->current_summation_delivered != cluster->last_persisted_energy) &&
        now - last >= interval) {
        metering_cluster_save_energy(cluster);
    }
}

void metering_cluster_report(metering_cluster_t *cluster) {
    (void)cluster;
}

void metering_cluster_load_energy(metering_cluster_t *cluster) {
    if (!cluster)
        return;

    cluster->last_baseline_retry_time = hal_millis();

    metering_nv_data_t nv_data;
    hal_nvm_status_t   status = hal_nvm_read(
        NV_ITEM_ENERGY_ACCUMULATION(cluster->endpoint), sizeof(nv_data),
        (uint8_t *)&nv_data);
    if (status == HAL_NVM_SUCCESS) {
        cluster->current_summation_delivered = nv_data.accumulated_energy_wh;
        cluster->last_reported_energy        = nv_data.accumulated_energy_wh;
        cluster->energy_baseline_valid       = 1;
        printf("Metering: Loaded energy %llu Wh from NVM\r\n",
               (unsigned long long)cluster->current_summation_delivered);
        cluster->last_energy_value_seeded = 0;
    } else if (status == HAL_NVM_NOT_FOUND) {
        cluster->current_summation_delivered = 0;
        cluster->last_reported_energy        = 0;
        cluster->energy_baseline_valid       = 1;
        cluster->last_energy_value_seeded    = 0;
        printf("Metering: No energy in NVM, starting from 0\r\n");
    } else {
        /* Preserve storage and keep the baseline unknown.  A future update or
         * controlled checkpoint will retry the read; until then no save is
         * allowed to replace the last good record with a zero-based total. */
        cluster->current_summation_delivered = METERING_SUMMATION_UNTRUSTED;
        cluster->energy_baseline_valid       = 0;
        printf("Metering: NVM read failed; reporting untrusted, NVM record preserved\r\n");
    }
    if (cluster->energy_baseline_valid && cluster->meter) {
        cluster->last_persisted_energy = cluster->current_summation_delivered;
        cluster->persistence_retry_pending = 0;
        energy_meter_data_t data;
        memset(&data, 0, sizeof(data));
        energy_meter_get_data(cluster->meter, &data);
        if (data.valid) {
            /* Capture the live counter when the baseline is restored, rather
             * than discarding energy accrued before the next update. */
            cluster->last_energy_value        = data.energy;
            cluster->last_energy_value_seeded = 1;
        }
    }
}

bool metering_cluster_save_energy(metering_cluster_t *cluster) {
    if (!cluster || !cluster->energy_baseline_valid)
        return false;

    metering_nv_data_t nv_data = {
        .accumulated_energy_wh = cluster->current_summation_delivered,
    };
    cluster->last_nvm_attempt_time = hal_millis();
    if (hal_nvm_write(NV_ITEM_ENERGY_ACCUMULATION(cluster->endpoint),
                      sizeof(nv_data), (uint8_t *)&nv_data) != HAL_NVM_SUCCESS) {
        cluster->persistence_retry_pending = 1;
        if (cluster->persistence_failures != UINT32_MAX)
            cluster->persistence_failures++;
        return false;
    }
    cluster->last_persisted_energy = cluster->current_summation_delivered;
    cluster->last_nvm_save_time = cluster->last_nvm_attempt_time;
    cluster->persistence_retry_pending = 0;
    return true;
}

void metering_cluster_reset_energy(metering_cluster_t *cluster) {
    if (!cluster)
        return;

    cluster->current_summation_delivered = 0;
    cluster->last_energy_value           = 0;
    cluster->last_energy_value_seeded    = (cluster->meter != NULL);
    cluster->last_reported_energy        = 0;

    /* Explicit user reset is the only operation allowed to replace an unknown
     * baseline intentionally. */
    cluster->energy_baseline_valid = 1;
    if (cluster->meter)
        energy_meter_reset_energy(cluster->meter);
    metering_cluster_save_energy(cluster);
}

bool metering_cluster_checkpoint(void) {
    if (!g_metering_cluster)
        return true;

    if (!g_metering_cluster->energy_baseline_valid) {
        metering_cluster_load_energy(g_metering_cluster);
        if (!g_metering_cluster->energy_baseline_valid) {
            printf("Metering checkpoint deferred; energy baseline unavailable\r\n");
            return false;
        }
    }

    metering_cluster_update(g_metering_cluster);
    if (!g_metering_cluster->energy_baseline_valid)
        return false;

    metering_nv_data_t desired = {
        .accumulated_energy_wh = g_metering_cluster->current_summation_delivered,
    };
    metering_nv_data_t saved;
    uint8_t            item = NV_ITEM_ENERGY_ACCUMULATION(g_metering_cluster->endpoint);

    g_metering_cluster->last_nvm_attempt_time = hal_millis();
    if (hal_nvm_write(item, sizeof(desired), (uint8_t *)&desired) != HAL_NVM_SUCCESS ||
        hal_nvm_read(item, sizeof(saved), (uint8_t *)&saved) != HAL_NVM_SUCCESS ||
        saved.accumulated_energy_wh != desired.accumulated_energy_wh) {
        g_metering_cluster->persistence_retry_pending = 1;
        if (g_metering_cluster->persistence_failures != UINT32_MAX)
            g_metering_cluster->persistence_failures++;
        printf("Metering checkpoint failed; controlled reboot deferred\r\n");
        return false;
    }
    g_metering_cluster->last_nvm_save_time = hal_millis();
    g_metering_cluster->last_persisted_energy = desired.accumulated_energy_wh;
    g_metering_cluster->persistence_retry_pending = 0;
    return true;
}

void metering_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                     uint16_t attribute_id) {
    if (attribute_id == ZCL_ATTR_METERING_CUST_RESET_ENERGY &&
        g_metering_cluster && g_metering_cluster->endpoint == endpoint) {
        metering_cluster_reset_energy(g_metering_cluster);
    }
}
