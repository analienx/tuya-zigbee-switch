#include "ts0505b_light_state.h"

#include <stddef.h>

#define TS0505B_DEFAULT_LEVEL 254u
#define TS0505B_DEFAULT_CT_MIRED 370u
#define TS0505B_CT_MIN_MIRED 153u
#define TS0505B_CT_MAX_MIRED 500u

static uint16_t clamp_ct(uint16_t mired)
{
  if (mired < TS0505B_CT_MIN_MIRED) {
    return TS0505B_CT_MIN_MIRED;
  }
  if (mired > TS0505B_CT_MAX_MIRED) {
    return TS0505B_CT_MAX_MIRED;
  }
  return mired;
}

void ts0505b_light_state_init(ts0505b_light_state_t *state)
{
  if (state == NULL) {
    return;
  }
  state->on = false;
  state->level = TS0505B_DEFAULT_LEVEL;
  state->mode = TS0505B_COLOR_MODE_CT;
  state->color_temperature_mired = TS0505B_DEFAULT_CT_MIRED;
  state->hue = 0u;
  state->saturation = 0u;
  state->x = 0u;
  state->y = 0u;
}

void ts0505b_light_apply_on_off(ts0505b_light_state_t *state, bool on)
{
  if (state == NULL) {
    return;
  }
  state->on = on;
}

void ts0505b_light_apply_level(ts0505b_light_state_t *state, uint8_t level, bool with_on_off)
{
  if (state == NULL) {
    return;
  }
  state->level = level;
  if (with_on_off) {
    state->on = (level != 0u);
  }
}
void ts0505b_light_apply_color_temperature(ts0505b_light_state_t *state, uint16_t mired)
{
  if (state == NULL) {
    return;
  }
  state->mode = TS0505B_COLOR_MODE_CT;
  state->color_temperature_mired = clamp_ct(mired);
}

void ts0505b_light_apply_hs(ts0505b_light_state_t *state, uint8_t hue, uint8_t saturation)
{
  if (state == NULL) {
    return;
  }
  state->mode = TS0505B_COLOR_MODE_HS;
  state->hue = hue;
  state->saturation = saturation;
}

void ts0505b_light_apply_xy(ts0505b_light_state_t *state, uint16_t x, uint16_t y)
{
  if (state == NULL) {
    return;
  }
  state->mode = TS0505B_COLOR_MODE_XY;
  state->x = x;
  state->y = y;
}
void ts0505b_light_render(const ts0505b_light_state_t *state, ts0505b_light_output_t *output)
{
  if (state == NULL || output == NULL) {
    return;
  }
  output->enabled = state->on;
  output->level = state->level;
  output->mode = state->mode;
  output->color_temperature_mired = state->color_temperature_mired;
  output->hue = state->hue;
  output->saturation = state->saturation;
  output->x = state->x;
  output->y = state->y;
}

bool ts0505b_light_state_is_consistent(const ts0505b_light_state_t *state, const ts0505b_light_output_t *output)
{
  if (state == NULL || output == NULL) {
    return false;
  }
  return output->enabled == state->on
         && output->level == state->level
         && output->mode == state->mode
         && output->color_temperature_mired == state->color_temperature_mired
         && output->hue == state->hue
         && output->saturation == state->saturation
         && output->x == state->x
         && output->y == state->y;
}
