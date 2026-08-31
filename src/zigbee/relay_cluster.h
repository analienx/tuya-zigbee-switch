#ifndef _RELAY_CLUSTER_H_
#define _RELAY_CLUSTER_H_

#include "base_components/led.h"
#include "base_components/relay.h"
#include <stdbool.h>
#include <stdint.h>

#include "hal/zigbee.h"

typedef struct {
    uint8_t              relay_idx;
    uint8_t              endpoint;
    uint8_t              startup_mode;
    uint8_t              indicator_led_mode;
    uint8_t              physical_relay_mode;
    hal_zigbee_attribute attr_infos[5];
    relay_t *            relay;
    led_t *              indicator_led;
    uint8_t              indicator_state;
} zigbee_relay_cluster;

void relay_cluster_add_to_endpoint(zigbee_relay_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint);

void relay_cluster_on(zigbee_relay_cluster *cluster);
void relay_cluster_off(zigbee_relay_cluster *cluster);
void relay_cluster_toggle(zigbee_relay_cluster *cluster);

void relay_cluster_report(zigbee_relay_cluster *cluster);

// Verified NVM helpers for device-specific migrations. They run before
// parse_config(), i.e. before the clusters exist, and operate purely on the
// stored NVM records. Every write is read back and verified.
bool relay_cluster_nv_set_indicator_safety(uint8_t relay_idx);
bool relay_cluster_nv_ensure_physical_mode(uint8_t relay_idx, uint8_t mode);
bool relay_cluster_nv_delete_physical_mode(uint8_t relay_idx);

void update_relay_clusters();

void relay_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                  uint16_t attribute_id);

#endif
