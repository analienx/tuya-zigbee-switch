#ifndef NC_PORT_TLSR_H
#define NC_PORT_TLSR_H
#include "tl_common.h"
#include "zcl_include.h"
#include "nc.h"
void nc_rx(void *message);
void nc_aps_confirm(void *confirm);
status_t nc_cluster_register(u8 ep, u16 manufacturer, u8 count,
                            const zclAttrInfo_t attrs[], cluster_forAppCb_t callback);
extern const zclAttrInfo_t nc_attrs[2];
#endif
