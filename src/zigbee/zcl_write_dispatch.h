#ifndef _ZCL_WRITE_DISPATCH_H_
#define _ZCL_WRITE_DISPATCH_H_

#include <stdbool.h>
#include <stdint.h>

#include "hal/zigbee.h"

/* Foundation-write dispatch guard (SDK-independent, host-testable).
 *
 * The Telink SDK invokes its application hook for every parsed write record
 * without reporting per-record success (SDK V3.7.2.0 zcl.c:
 * zcl_writeHandler records statuses only in the response buffer, which is
 * freed before the hook runs). This predicate mirrors the SDK acceptance
 * rule (zcl_attrWrite plus the HAL registration skips) so the HAL only
 * dispatches records the SDK actually wrote: endpoint and cluster
 * registered, attribute registered with a matching type, writable access,
 * and non-NULL storage. The OTA cluster is never registered with the SDK
 * and always fails.
 *
 * For WriteUndivided the caller must require every record to pass (the SDK
 * applies all or nothing); Write and WriteNoRsp dispatch per record.
 */
bool zcl_write_record_applied(uint8_t endpoint, uint16_t cluster_id,
                              uint16_t attr_id, uint8_t data_type,
                              hal_zigbee_endpoint *endpoints,
                              uint8_t endpoints_count);

#endif
