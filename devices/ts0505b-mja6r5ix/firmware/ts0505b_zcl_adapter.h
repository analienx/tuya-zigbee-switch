#ifndef TS0505B_ZCL_ADAPTER_H
#define TS0505B_ZCL_ADAPTER_H

#include <stdint.h>

#include "ts0505b_light_state.h"

void ts0505b_zcl_sync_output(uint8_t endpoint);
void ts0505b_board_apply_output(const ts0505b_light_output_t *output);

#endif
