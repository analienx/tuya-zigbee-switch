#include <assert.h>
#include <stdio.h>

#include "ts0505b_light_state.h"

static void assert_consistent(const ts0505b_light_state_t *state)
{
  ts0505b_light_output_t output;
  ts0505b_light_render(state, &output);
  assert(ts0505b_light_state_is_consistent(state, &output));
  assert(output.enabled == state->on);
}

int main(void)
{
  ts0505b_light_state_t state;
  ts0505b_light_output_t output;
  ts0505b_light_state_init(&state);
  assert(!state.on);
  assert(state.level == 254u);
  assert(state.mode == TS0505B_COLOR_MODE_CT);
  assert_consistent(&state);

  ts0505b_light_apply_level(&state, 50u, true);
  assert(state.on && state.level == 50u);
  assert_consistent(&state);
  ts0505b_light_apply_on_off(&state, false);
  ts0505b_light_apply_level(&state, 3u, false);
  assert(!state.on && state.level == 3u);
  ts0505b_light_render(&state, &output);
  assert(!output.enabled);
  assert_consistent(&state);

  ts0505b_light_apply_level(&state, 3u, true);
  assert(state.on && state.level == 3u);
  ts0505b_light_render(&state, &output);
  assert(output.enabled && output.level == 3u);
  assert_consistent(&state);

  ts0505b_light_apply_level(&state, 0u, true);
  assert(!state.on && state.level == 0u);
  assert_consistent(&state);

  ts0505b_light_apply_on_off(&state, true);
  ts0505b_light_render(&state, &output);
  assert(output.enabled && output.level == 0u);
  assert_consistent(&state);

  ts0505b_light_apply_level(&state, 127u, false);
  assert(state.on && state.level == 127u);
  assert_consistent(&state);
  ts0505b_light_apply_color_temperature(&state, 100u);
  assert(state.mode == TS0505B_COLOR_MODE_CT);
  assert(state.color_temperature_mired == 153u);
  ts0505b_light_apply_color_temperature(&state, 600u);
  assert(state.color_temperature_mired == 500u);
  assert_consistent(&state);

  ts0505b_light_apply_hs(&state, 42u, 200u);
  assert(state.mode == TS0505B_COLOR_MODE_HS);
  assert(state.hue == 42u && state.saturation == 200u);
  assert_consistent(&state);

  ts0505b_light_apply_xy(&state, 12345u, 45678u);
  assert(state.mode == TS0505B_COLOR_MODE_XY);
  assert(state.x == 12345u && state.y == 45678u);
  assert_consistent(&state);

  ts0505b_light_apply_on_off(&state, false);
  ts0505b_light_render(&state, &output);
  assert(!output.enabled);
  assert(output.mode == TS0505B_COLOR_MODE_XY);
  assert(output.x == 12345u && output.y == 45678u);
  puts("light-state harness PASS");
  return 0;
}
