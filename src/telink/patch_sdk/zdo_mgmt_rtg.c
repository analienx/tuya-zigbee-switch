#include "zb_common.h"

#include "mgmt_rtg_codec.h"

#ifdef ZB_ROUTER_ROLE

static bool route_entry_is_valid(const nwk_routingTabEntry_t *entry) {
    return !(entry->dstAddr == 0xfffeu && entry->nextHopAddr == 0xfffeu &&
             entry->status == NWK_ROUTE_STATE_DISCOVERY_INACTIVE);
}

static uint8_t route_entry_count(void) {
    uint8_t count = 0;

    for (uint16_t i = 0; i < ROUTING_TABLE_SIZE && count < 0xffu; ++i) {
        if (route_entry_is_valid(&g_routingTab[i])) {
            ++count;
        }
    }
    return count;
}

void zdo_mgmtRtgIndicate(void *buf) {
    zb_buf_t *      zbuff = (zb_buf_t *)buf;
    aps_data_ind_t *ad    = (aps_data_ind_t *)buf;

    if (ad->asduLength < 2u) {
        zb_buf_free(zbuff);
        return;
    }

    const uint8_t seq_num       = ad->asdu[0];
    const uint8_t start_index   = ad->asdu[1];
    const uint8_t total_entries = route_entry_count();
    const uint8_t list_count    = mgmt_rtg_page_count(total_entries, start_index);
    const uint8_t response_len  =
        (uint8_t)(MGMT_RTG_RESPONSE_HEADER_SIZE +
                  list_count * MGMT_RTG_DESCRIPTOR_SIZE);

    zdo_zdp_req_t req;
    TL_SETSTRUCTCONTENT(req, 0);
    TL_BUF_INITIAL_ALLOC(zbuff, response_len, req.zdu, u8 *);

    uint8_t *ptr = req.zdu;
    *ptr++ = seq_num;
    *ptr++ = ZDO_SUCCESS;
    *ptr++ = total_entries;
    *ptr++ = start_index;
    *ptr++ = list_count;

    uint8_t logical_index = 0;
    uint8_t emitted       = 0;
    for (uint16_t i = 0; i < ROUTING_TABLE_SIZE && emitted < list_count; ++i) {
        const nwk_routingTabEntry_t *entry = &g_routingTab[i];
        if (!route_entry_is_valid(entry)) {
            continue;
        }
        if (logical_index++ < start_index) {
            continue;
        }
        const uint8_t status_flags = mgmt_rtg_encode_status(
            entry->status, entry->noRouteCache != 0, entry->manyToOne != 0,
            entry->routeRecordRequired != 0);
        mgmt_rtg_encode_descriptor(ptr, entry->dstAddr, status_flags,
                                   entry->nextHopAddr);
        ptr += MGMT_RTG_DESCRIPTOR_SIZE;
        ++emitted;
    }

    req.cluster_id          = MGMT_RTG_RSP_CLID;
    req.zduLen              = (uint8_t)(ptr - req.zdu);
    req.buff_addr           = buf;
    req.dst_addr_mode       = SHORT_ADDR_MODE;
    req.dst_nwk_addr        = ad->src_short_addr;
    req.zdoRspReceivedIndCb = NULL;

    zdo_send_req(&req);
    zb_buf_free((zb_buf_t *)req.buff_addr);
}

#endif
