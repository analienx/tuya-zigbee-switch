#include "telink_zigbee_hal.h"
#include "hal/timer.h"
#include "hal/bseed_recovery_core.h"

/* Wrap only attribute reports, at the binding fan-out boundary. Commands,
 * relay/level reports, protection, reads and group recipients are unaffected.
 * Store attribute identities, not sampled values; flush reads the live table.
 * No NVM, dynamic allocation, binding edits or SDK timer replacement. */
#define TELEMETRY_SLOTS    32u
typedef struct {
    uint16_t cluster, attr, profile;
    uint8_t src_ep, dst_ep;
    bool used, bound;
} held_telemetry_t;
static held_telemetry_t held_telemetry[TELEMETRY_SLOTS];
static bool telemetry_held, telemetry_flushing;
static uint32_t telemetry_flush_since, telemetry_last_send;

status_t __real_zcl_report(u8 src, epInfo_t *dst, u8 disable, u8 direction,
                          u8 seq, u16 manufacturer, u16 cluster, u16 attr,
                          u8 type, u8 *data);
status_t __real_zcl_reportAttrs(u8 src, epInfo_t *dst, u8 disable, u8 direction,
                               u8 seq, u16 manufacturer, u16 cluster,
                               zclReportCmd_t *attrs);

static bool telemetry_cluster(uint16_t cluster) {
    return cluster == ZCL_CLUSTER_MS_ELECTRICAL_MEASUREMENT ||
           cluster == ZCL_CLUSTER_SE_METERING;
}

static bool telemetry_coordinator(epInfo_t *dst) {
    if (dst->dstAddrMode == APS_SHORT_DSTADDR_WITHEP)
        return dst->dstAddr.shortAddr == 0u;
    if (dst->dstAddrMode == APS_LONG_DSTADDR_WITHEP) {
        uint16_t nwk;
        return zb_getNwkAddrByExtAddr(dst->dstAddr.extAddr, &nwk) == SUCCESS && nwk == 0u;
    }
    return false;
}

static bool telemetry_binding(aps_binding_entry_t *binding, u8 src, u16 cluster,
                              const epInfo_t *original, epInfo_t *dst) {
    if (!binding->used || binding->srcEp != src || binding->clusterId != cluster)
        return false;
    *dst = *original;
    dst->dstAddrMode = binding->dstAddrMode;
    if (binding->dstAddrMode == APS_LONG_DSTADDR_WITHEP) {
        memcpy(dst->dstAddr.extAddr, binding->dstExtAddrInfo.extAddr, 8);
        dst->dstEp = binding->dstExtAddrInfo.dstEp;
    } else if (binding->dstAddrMode == APS_SHORT_GROUPADDR_NOEP) {
        dst->dstAddr.shortAddr = binding->groupAddr;
        dst->dstEp = 0u;
    } else {
        return false;
    }
    return true;
}

static bool telemetry_remember(u8 src, epInfo_t *dst, u16 cluster, u16 attr, bool bound) {
    held_telemetry_t *slot = NULL;
    for (unsigned i = 0; i < TELEMETRY_SLOTS; i++) {
        held_telemetry_t *entry = &held_telemetry[i];
        if (entry->used && entry->src_ep == src && entry->dst_ep == dst->dstEp &&
            entry->profile == dst->profileId && entry->cluster == cluster &&
            entry->attr == attr && entry->bound == bound)
            return true;
        if (!entry->used)
            slot = entry;
    }
    if (!slot || !zcl_findAttribute(src, cluster, attr))
        return false; /* unregistered/full: preserve the original send */
    *slot = (held_telemetry_t){cluster, attr, dst->profileId, src, dst->dstEp, true, bound};
    return true;
}

static bool telemetry_should_hold(void) {
    if (telink_zigbee_hal_hold_coordinator_telemetry()) {
        telemetry_held = true;
        telemetry_flushing = false;
    } else if (telemetry_held) {
        telemetry_held = false;
        telemetry_flushing = true;
        telemetry_flush_since = hal_millis();
        telemetry_last_send = hal_millis() - 1000u;
    }
    return telemetry_held || telemetry_flushing;
}

static status_t telemetry_report_one(u8 src, epInfo_t *dst, u8 disable, u8 direction,
                                    u8 seq, u16 manufacturer, u16 cluster, u16 attr,
                                    u8 type, u8 *data, bool bound) {
    if (telemetry_coordinator(dst) && telemetry_remember(src, dst, cluster, attr, bound))
        return SUCCESS; /* coalesced locally, not evidence of delivery */
    return __real_zcl_report(src, dst, disable, direction, seq, manufacturer,
                            cluster, attr, type, data);
}

status_t __wrap_zcl_report(u8 src, epInfo_t *dst, u8 disable, u8 direction,
                          u8 seq, u16 manufacturer, u16 cluster, u16 attr,
                          u8 type, u8 *data) {
    if (!telemetry_cluster(cluster) || manufacturer != MANUFACTURER_CODE_NONE ||
        direction != ZCL_FRAME_SERVER_CLIENT_DIR || !telemetry_should_hold())
        return __real_zcl_report(src, dst, disable, direction, seq, manufacturer,
                                cluster, attr, type, data);
    if (dst->dstAddrMode != APS_DSTADDR_EP_NOTPRESETNT)
        return telemetry_report_one(src, dst, disable, direction, seq, manufacturer,
                                    cluster, attr, type, data, false);
    status_t result = SUCCESS;
    for (unsigned i = 0; i < APS_BINDING_TABLE_SIZE; i++) {
        epInfo_t peer;
        if (!telemetry_binding(&g_apsBindingTbl[i], src, cluster, dst, &peer))
            continue;
        status_t status = telemetry_report_one(src, &peer, disable, direction, seq,
                                               manufacturer, cluster, attr, type, data, true);
        if (status != SUCCESS)
            result = status;
    }
    return result;
}

status_t __wrap_zcl_reportAttrs(u8 src, epInfo_t *dst, u8 disable, u8 direction,
                               u8 seq, u16 manufacturer, u16 cluster,
                               zclReportCmd_t *attrs) {
    if (!telemetry_cluster(cluster) || manufacturer != MANUFACTURER_CODE_NONE ||
        direction != ZCL_FRAME_SERVER_CLIENT_DIR || !telemetry_should_hold())
        return __real_zcl_reportAttrs(src, dst, disable, direction, seq, manufacturer, cluster, attrs);
    status_t result = SUCCESS;
    for (unsigned i = 0; i < attrs->numAttr; i++) {
        zclReport_t *attr = &attrs->attrList[i];
        status_t status = __wrap_zcl_report(src, dst, disable, direction, seq, manufacturer,
                                           cluster, attr->attrID, attr->dataType, attr->attrData);
        if (status != SUCCESS)
            result = status;
    }
    return result;
}

static bool telemetry_still_bound(const held_telemetry_t *entry, epInfo_t *dst) {
    if (!entry->bound)
        return true;
    for (unsigned i = 0; i < APS_BINDING_TABLE_SIZE; i++) {
        epInfo_t peer;
        if (telemetry_binding(&g_apsBindingTbl[i], entry->src_ep, entry->cluster, dst, &peer) &&
            peer.dstEp == entry->dst_ep && telemetry_coordinator(&peer))
            return true;
    }
    return false;
}

void telink_zigbee_hal_telemetry_tick(void) {
    (void)telemetry_should_hold();
    uint32_t now = hal_millis();
    if (telemetry_held || !telemetry_flushing ||
        (uint32_t)(now - telemetry_flush_since) < bseed_recovery_action_stagger_ms(
            g_zbMacPib.extAddress, 0x54454c45u, 60001u) ||
        (uint32_t)(now - telemetry_last_send) < 1000u)
        return;
    for (unsigned i = 0; i < TELEMETRY_SLOTS; i++) {
        held_telemetry_t *entry = &held_telemetry[i];
        if (!entry->used)
            continue;
        epInfo_t dst = {0};
        dst.profileId = entry->profile;
        dst.dstAddrMode = APS_SHORT_DSTADDR_WITHEP;
        dst.dstEp = entry->dst_ep;
        zclAttrInfo_t *attr = zcl_findAttribute(entry->src_ep, entry->cluster, entry->attr);
        if (!attr || !telemetry_still_bound(entry, &dst)) {
            entry->used = false;
            continue;
        }
        telemetry_last_send = now;
        if (__real_zcl_report(entry->src_ep, &dst, TRUE, ZCL_FRAME_SERVER_CLIENT_DIR,
                              ZCL_SEQ_NUM, MANUFACTURER_CODE_NONE, entry->cluster,
                              attr->id, attr->type, attr->data) == SUCCESS)
            entry->used = false;
        return; /* at most one current-value attribute per second */
    }
    telemetry_flushing = false;
}
