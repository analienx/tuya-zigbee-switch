#include "zcl_write_dispatch.h"

#include "zigbee/consts.h"

bool zcl_write_record_applied(uint8_t endpoint, uint16_t cluster_id,
                              uint16_t attr_id, uint8_t data_type,
                              hal_zigbee_endpoint *endpoints,
                              uint8_t endpoints_count) {
    if (cluster_id == ZCL_CLUSTER_OTA_BOOTLOAD) {
        return false;
    }
    const hal_zigbee_cluster *cluster =
        hal_zigbee_find_cluster(endpoints, endpoints_count, endpoint,
                                cluster_id);
    if (cluster == NULL) {
        return false;
    }
    for (int i = 0; i < cluster->attribute_count; i++) {
        const hal_zigbee_attribute *attr = &cluster->attributes[i];
        if (attr->attribute_id != attr_id ||
            attr->data_type_id != data_type) {
            continue;
        }
        return attr->flag == ATTR_WRITABLE && attr->value != NULL;
    }
    return false;
}
