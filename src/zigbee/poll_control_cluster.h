#ifndef _POLL_CONTROL_CLUSTER_H_
#define _POLL_CONTROL_CLUSTER_H_

#ifdef END_DEVICE

#include "hal/tasks.h"
#include "hal/zigbee.h"
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    // ZCL attributes
    uint32_t             check_in_interval;   // quarter-seconds, RW
    uint32_t             long_poll_interval;  // quarter-seconds, R
    uint16_t             short_poll_interval; // quarter-seconds, R
    uint16_t             fast_poll_timeout;   // quarter-seconds, RW
    uint16_t             cluster_revision;

    // Runtime state
    uint8_t              endpoint;
    bool                 in_fast_poll;
    uint32_t             fast_poll_end_ms;

    // Tasks
    hal_task_t           check_in_task;

    // ZCL attribute table
    hal_zigbee_attribute attr_infos[5]; // 4 mandatory + cluster_revision
} zigbee_poll_control_cluster;

#ifdef BSEED_MAINS_CLIENT
// Mains clients are end devices only in the topology sense. Their receiver is
// permanently on, so exposing Poll Control or changing the MAC poll interval
// would recreate the sleepy-end-device behavior this role is intended to avoid.
static inline void poll_control_cluster_add_to_endpoint(
    zigbee_poll_control_cluster *cluster, hal_zigbee_endpoint *endpoint,
    bool is_battery_device) {
    (void)cluster;
    (void)endpoint;
    (void)is_battery_device;
}

static inline void poll_control_cluster_update(void) {
}

static inline void poll_control_cluster_callback_attr_write(
    uint16_t attribute_id) {
    (void)attribute_id;
}
#else
void poll_control_cluster_add_to_endpoint(zigbee_poll_control_cluster *cluster,
                                          hal_zigbee_endpoint *endpoint,
                                          bool is_battery_device);
void poll_control_cluster_update(void);
void poll_control_cluster_callback_attr_write(uint16_t attribute_id);
#endif

#endif // END_DEVICE
#endif // _POLL_CONTROL_CLUSTER_H_
