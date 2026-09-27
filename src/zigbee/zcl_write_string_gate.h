#ifndef _ZCL_WRITE_STRING_GATE_H_
#define _ZCL_WRITE_STRING_GATE_H_

#include <stdbool.h>
#include <stdint.h>

#include "hal/zigbee.h"

/* Foundation-write string guard (SDK-independent, host-testable).
 *
 * The Telink SDK copies foundation-write record bytes into the registered
 * attribute destination BEFORE any application hook runs, trusting the
 * record's declared length (SDK V3.7.2.0 zcl.c: zcl_setAttrVal memcpy).
 * Call this from the AF receive path before zcl_rx_handler; when it returns
 * false the message must be dropped (and its buffer released) without
 * reaching the SDK.
 *
 * Drops when the message is a profile-wide Write/WriteUndivided/WriteNoRsp
 * to a registered server cluster AND any record is structurally truncated,
 * unsizable (STRUCT/unknown), or a string whose declared length exceeds the
 * remaining message bytes or the registered storage capacity of the
 * targeted writable same-type string attribute. All other traffic passes
 * through untouched. Memory safety outranks mixed-request liveness: a valid
 * record bundled with an oversized/truncated string is not applied.
 */
bool zcl_write_string_gate_allows(const uint8_t *asdu, uint16_t asdu_len,
                                  uint8_t endpoint, uint16_t cluster_id,
                                  hal_zigbee_endpoint *endpoints,
                                  uint8_t endpoints_count);

#endif
