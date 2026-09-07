#ifndef _BASIC_CLUSTER_H_
#define _BASIC_CLUSTER_H_

#include "hal/zigbee.h"

#include <stddef.h>

typedef struct {
    uint8_t              deviceEnable;
    char                 manuName[32];
    char                 modelId[32];
    hal_zigbee_attribute attr_infos[14];
} zigbee_basic_cluster;

void basic_cluster_add_to_endpoint(zigbee_basic_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint);

void basic_cluster_callback_attr_write_trampoline(uint16_t attribute_id);

hal_zigbee_cmd_result_t basic_cluster_callback_trampoline(
    uint8_t endpoint, uint16_t cluster_id, uint8_t command_id,
    void *cmd_payload, uint16_t cmd_payload_len);

#endif
