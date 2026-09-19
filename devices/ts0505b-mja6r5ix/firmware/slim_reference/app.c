/* Experimental, dark TS0505B router application. No production LED outputs. */
#include "app/framework/include/af.h"
#include "ts0505b_zcl_adapter.h"
#include "network-steering.h"

#define LIGHT_ENDPOINT 1
#define JOIN_RETRY_MS 30000UL
static sl_zigbee_af_event_t joining_event;

static void joining_event_handler(sl_zigbee_af_event_t *event)
{
  sl_zigbee_af_event_set_inactive(event);
  if (sl_zigbee_af_network_state() != SL_ZIGBEE_JOINED_NETWORK) {
    sl_zigbee_af_network_steering_autostart();
  }
}

void sl_zigbee_af_main_init_cb(void)
{
  sl_zigbee_af_event_init(&joining_event, joining_event_handler);
  sl_zigbee_af_event_set_delay_ms(&joining_event, 3000UL);
}

void sl_zigbee_af_stack_status_cb(sl_status_t status)
{
  if (status == SL_STATUS_NETWORK_DOWN) {
    sl_zigbee_af_event_set_delay_ms(&joining_event, JOIN_RETRY_MS);
  }
}
void sl_zigbee_af_network_steering_complete_cb(sl_status_t status,
                                                uint8_t totalBeacons,
                                                uint8_t joinAttempts,
                                                uint8_t finalState)
{
  (void)totalBeacons;
  (void)joinAttempts;
  (void)finalState;
  if (status != SL_STATUS_OK) {
    sl_zigbee_af_event_set_delay_ms(&joining_event, JOIN_RETRY_MS);
  }
}

void sl_zigbee_af_post_attribute_change_cb(uint8_t endpoint,
                                            sl_zigbee_af_cluster_id_t clusterId,
                                            sl_zigbee_af_attribute_id_t attributeId,
                                            uint8_t mask,
                                            uint16_t manufacturerCode,
                                            uint8_t type,
                                            uint8_t size,
                                            uint8_t *value)
{
  (void)attributeId;
  (void)manufacturerCode;
  (void)type;
  (void)size;
  (void)value;
  if (endpoint == LIGHT_ENDPOINT && mask == CLUSTER_MASK_SERVER &&
      (clusterId == ZCL_ON_OFF_CLUSTER_ID ||
       clusterId == ZCL_LEVEL_CONTROL_CLUSTER_ID ||
       clusterId == ZCL_COLOR_CONTROL_CLUSTER_ID)) {
    ts0505b_zcl_sync_output(endpoint);
  }
}

void sl_zigbee_af_on_off_cluster_server_post_init_cb(uint8_t endpoint)
{
  if (endpoint == LIGHT_ENDPOINT) {
    ts0505b_zcl_sync_output(endpoint);
  }
}

void sl_zigbee_af_radio_needs_calibrating_cb(void)
{
#ifndef EZSP_HOST
  sl_mac_calibrate_current_channel();
#endif
}
