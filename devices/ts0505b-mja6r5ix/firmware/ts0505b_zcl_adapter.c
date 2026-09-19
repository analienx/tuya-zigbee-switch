#include "ts0505b_zcl_adapter.h"

#include "app/framework/include/af.h"

static bool read_u8(uint8_t endpoint, sl_zigbee_af_cluster_id_t cluster,
                    sl_zigbee_af_attribute_id_t attribute, uint8_t *value)
{
  return sl_zigbee_af_read_server_attribute(endpoint, cluster, attribute,
                                             value, sizeof(*value))
         == SL_ZIGBEE_ZCL_STATUS_SUCCESS;
}

static bool read_u16(uint8_t endpoint, sl_zigbee_af_cluster_id_t cluster,
                     sl_zigbee_af_attribute_id_t attribute, uint16_t *value)
{
  return sl_zigbee_af_read_server_attribute(endpoint, cluster, attribute,
                                             (uint8_t *)value, sizeof(*value))
         == SL_ZIGBEE_ZCL_STATUS_SUCCESS;
}

__attribute__((weak)) void ts0505b_board_apply_output(const ts0505b_light_output_t *output)
{
  (void)output;
}
void ts0505b_zcl_sync_output(uint8_t endpoint)
{
  ts0505b_light_state_t state;
  ts0505b_light_output_t output;
  uint8_t on_off = 0u;
  uint8_t level = 254u;
  uint8_t color_mode = SL_ZIGBEE_ZCL_COLOR_MODE_COLOR_TEMPERATURE;
  uint8_t hue = 0u;
  uint8_t saturation = 0u;
  uint16_t x = 0u;
  uint16_t y = 0u;
  uint16_t color_temperature = 370u;

  ts0505b_light_state_init(&state);
  if (read_u8(endpoint, ZCL_ON_OFF_CLUSTER_ID, ZCL_ON_OFF_ATTRIBUTE_ID, &on_off)) {
    ts0505b_light_apply_on_off(&state, on_off != 0u);
  }
  if (read_u8(endpoint, ZCL_LEVEL_CONTROL_CLUSTER_ID,
              ZCL_CURRENT_LEVEL_ATTRIBUTE_ID, &level)) {
    ts0505b_light_apply_level(&state, level, false);
  }
  if (read_u8(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
              ZCL_COLOR_CONTROL_COLOR_MODE_ATTRIBUTE_ID, &color_mode)) {
    if (color_mode == SL_ZIGBEE_ZCL_COLOR_MODE_CURRENT_HUE_AND_CURRENT_SATURATION
        && read_u8(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
                   ZCL_COLOR_CONTROL_CURRENT_HUE_ATTRIBUTE_ID, &hue)
        && read_u8(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
                   ZCL_COLOR_CONTROL_CURRENT_SATURATION_ATTRIBUTE_ID, &saturation)) {
      ts0505b_light_apply_hs(&state, hue, saturation);
    } else if (color_mode == SL_ZIGBEE_ZCL_COLOR_MODE_CURRENT_X_AND_CURRENT_Y
               && read_u16(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
                           ZCL_COLOR_CONTROL_CURRENT_X_ATTRIBUTE_ID, &x)
               && read_u16(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
                           ZCL_COLOR_CONTROL_CURRENT_Y_ATTRIBUTE_ID, &y)) {
      ts0505b_light_apply_xy(&state, x, y);
    } else if (color_mode == SL_ZIGBEE_ZCL_COLOR_MODE_COLOR_TEMPERATURE
               && read_u16(endpoint, ZCL_COLOR_CONTROL_CLUSTER_ID,
                           ZCL_COLOR_CONTROL_COLOR_TEMPERATURE_ATTRIBUTE_ID,
                           &color_temperature)) {
      ts0505b_light_apply_color_temperature(&state, color_temperature);
    }
  }

  ts0505b_light_render(&state, &output);
  if (ts0505b_light_state_is_consistent(&state, &output)) {
    ts0505b_board_apply_output(&output);
  }
}
