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
    uint8_t              binding_intent_state;
    uint8_t              current_level;
    hal_zigbee_attribute level_attr_infos[1];
    hal_zigbee_attribute attr_infos[6];
    relay_t *            relay;
    led_t *              indicator_led;
    uint8_t              indicator_state;

    /* Protection-trip latch: while set, the physical output is forced off
     * regardless of physical_relay_mode (including detached/Always-on).
     * Boot-volatile. Ordinary ON/OFF commands never clear it; only the
     * overload state machine's explicit reconnect path may rearm. */
    uint8_t              protection_tripped;
} zigbee_relay_cluster;

void relay_cluster_add_to_endpoint(zigbee_relay_cluster *cluster,
                                   hal_zigbee_endpoint *endpoint);

void relay_cluster_on(zigbee_relay_cluster *cluster);
void relay_cluster_off(zigbee_relay_cluster *cluster);
void relay_cluster_toggle(zigbee_relay_cluster *cluster);

/* Protection-trip cut-off: forces logical OFF and drives the physical output
 * off in every physical mode, then latches. Ordinary commands cannot clear the
 * latch. Startup/reboot starts unlatched; a persistent overload re-trips once
 * monitoring resumes. */
void relay_cluster_protection_trip(zigbee_relay_cluster *cluster);

/* Called only by the overload state machine after its reconnect delay/retry
 * policy admits a retry. Clears the physical trip latch and restores ON using
 * the configured physical mode. */
void relay_cluster_protection_rearm(zigbee_relay_cluster *cluster);

/* Effective physical energization: 0 while protection-tripped, otherwise the
 * mode-resolved output state. Protection monitoring must use this, never the
 * logical state alone: in detached modes the contact can be energized while
 * the logical state is off and vice versa. */
uint8_t relay_cluster_is_physically_on(const zigbee_relay_cluster *cluster);

// Update only the locally tracked/intended direct-binding On/Off state.
// This never emits a Zigbee command and never changes logical or mains state.
void relay_cluster_set_binding_intent(zigbee_relay_cluster *cluster,
                                      uint8_t state);

void relay_cluster_report(zigbee_relay_cluster *cluster);

// Verified NVM helpers for device-specific migrations. They run before
// parse_config(), i.e. before the clusters exist, and operate purely on the
// stored NVM records. Every write is read back and verified.
bool relay_cluster_nv_set_indicator_safety(uint8_t relay_idx);
bool relay_cluster_nv_set_indicator_mode(uint8_t relay_idx, uint8_t mode);
bool relay_cluster_nv_ensure_physical_mode(uint8_t relay_idx, uint8_t mode);
bool relay_cluster_nv_ensure_valid_physical_mode(uint8_t relay_idx,
                                                 uint8_t safe_default_mode);
bool relay_cluster_nv_delete_physical_mode(uint8_t relay_idx);

void update_relay_clusters();

void relay_cluster_callback_attr_write_trampoline(uint8_t endpoint,
                                                  uint16_t attribute_id);

#endif
