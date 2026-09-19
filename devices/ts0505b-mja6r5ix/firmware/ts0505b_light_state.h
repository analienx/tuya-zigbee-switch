#ifndef TS0505B_LIGHT_STATE_H
#define TS0505B_LIGHT_STATE_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  TS0505B_COLOR_MODE_CT = 0,
  TS0505B_COLOR_MODE_HS = 1,
  TS0505B_COLOR_MODE_XY = 2,
} ts0505b_color_mode_t;

typedef struct {
  bool on;
  uint8_t level;
  ts0505b_color_mode_t mode;
  uint16_t color_temperature_mired;
  uint8_t hue;
  uint8_t saturation;
  uint16_t x;
  uint16_t y;
} ts0505b_light_state_t;
typedef struct {
  bool enabled;
  uint8_t level;
  ts0505b_color_mode_t mode;
  uint16_t color_temperature_mired;
  uint8_t hue;
  uint8_t saturation;
  uint16_t x;
  uint16_t y;
} ts0505b_light_output_t;

void ts0505b_light_state_init(ts0505b_light_state_t *state);
void ts0505b_light_apply_on_off(ts0505b_light_state_t *state, bool on);
void ts0505b_light_apply_level(ts0505b_light_state_t *state, uint8_t level, bool with_on_off);
void ts0505b_light_apply_color_temperature(ts0505b_light_state_t *state, uint16_t mired);
void ts0505b_light_apply_hs(ts0505b_light_state_t *state, uint8_t hue, uint8_t saturation);
void ts0505b_light_apply_xy(ts0505b_light_state_t *state, uint16_t x, uint16_t y);
void ts0505b_light_render(const ts0505b_light_state_t *state, ts0505b_light_output_t *output);
bool ts0505b_light_state_is_consistent(const ts0505b_light_state_t *state, const ts0505b_light_output_t *output);

#endif
