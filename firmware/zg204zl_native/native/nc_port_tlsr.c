#include "nc_port_tlsr.h"
#include "app_main.h"
#include "app_onoff.h"
#include "sensor_lx.h"
#include "zcl_illuminance_level_sensing.h"
#include "battery.h"
#include "nwk/includes/nwk_addr_map.h"

extern u32 rtcMs;
static ev_timer_event_t *native_timer;
static uint32_t last_timer_attempt = 0xffffffffu;

uint32_t nc_port_now(void) { return rtcMs; }
int nc_port_joined(void) { return zb_isDeviceJoinedNwk() && !g_sensorAppCtx.linkDown && bdb_isIdle(); }
int nc_port_master(void) { return g_zcl_onOffAttrs.onOff; }
int nc_port_occupied(void) { return zcl_occupAttr.occupancy != 0; }
uint16_t nc_port_lux_target(void) { return g_zcl_illuminanceAttrs.TargetLevelzLx; }
uint16_t nc_port_battery(void) { return measured_battery.mv; }
int nc_port_dark(void) {
#ifdef GPIO_LED
    gpio_write(GPIO_LED, LED_OFF);
#endif
    read_illumi_sensor();
    return g_zcl_illuminanceAttrs.levelStatus == ILSC_BELOW_TARGET ||
           g_zcl_illuminanceAttrs.levelStatus == ILSC_ON_TARGET;
}
int nc_port_nv_read(uint8_t raw[12]) {
    return nv_flashReadNew(1, NV_MODULE_APP, NV_ITEM_APP_NATIVE_CFG, 12, raw) == NV_SUCC;
}
int nc_port_nv_write(const uint8_t raw[12]) {
    return nv_flashWriteNew(1, NV_MODULE_APP, NV_ITEM_APP_NATIVE_CFG, 12, (u8 *)raw) == NV_SUCC;
}
int nc_port_binding(unsigned i, nc_binding_t *out) {
    aps_binding_entry_t *b;
    if (i >= APS_BINDING_TABLE_NUM) return 0;
    b = &g_apsBindingTbl[i];
    if (!b->used || b->srcEp != SENSOR_DEVICE_ENDPOINT ||
        (b->clusterId != 6 && b->clusterId != 8)) return 0;
    memset(out, 0, sizeof(*out));
    out->caps = b->clusterId == 8 ? NC_LEVEL : NC_ONOFF;
    if (b->dstAddrMode == APS_SHORT_GROUPADDR_NOEP) {
        out->addr.group = 1; nc_put16(out->addr.id, b->groupAddr);
    } else if (b->dstAddrMode == APS_LONG_DSTADDR_WITHEP) {
        memcpy(out->addr.id, b->dstExtAddrInfo.extAddr, 8);
        out->addr.ep = b->dstExtAddrInfo.dstEp;
    } else return 0;
    return 1;
}
uint8_t nc_port_sequence(void) { return ZCL_SEQ_NUM; }
uint8_t nc_port_send(const nc_addr_t *addr, uint16_t cluster,
                     const uint8_t *frame, unsigned length, int ack, uint8_t *aps) {
    epInfo_t target;
    memset(&target, 0, sizeof(target));
    target.profileId = HA_PROFILE_ID; target.dstEp = addr->ep;
    if (addr->group) {
        target.dstAddrMode = APS_SHORT_GROUPADDR_NOEP;
        target.dstAddr.shortAddr = nc_le16(addr->id);
    } else if (cluster == 0x000a) {
        target.dstAddrMode = APS_SHORT_DSTADDR_WITHEP; target.dstAddr.shortAddr = 0;
    } else {
        target.dstAddrMode = APS_LONG_DSTADDR_WITHEP;
        memcpy(target.dstAddr.extAddr, addr->id, 8);
    }
    if (ack && !addr->group) target.txOptions = APS_TX_OPT_ACK_TX;
    return af_dataSend(SENSOR_DEVICE_ENDPOINT, &target, cluster, length, (u8 *)frame, aps);
}
void nc_port_fast_poll(void) { app_fastPollWindow(3000); }

static s32 timer_fired(void *unused) {
    /* Wake the ordinary app pass; no ADC or TX inside a timer/confirm callback. */
    (void)unused; native_timer = 0; return -1;
}
void nc_port_arm(uint32_t delay) {
    uint32_t second = rtcMs / 1000u;
    if (!delay) {
        if (native_timer) TL_ZB_TIMER_CANCEL(&native_timer);
        return;
    }
    if (native_timer && native_timer->timeout <= delay) return;
    if (!native_timer && last_timer_attempt == second) return;
    if (native_timer) TL_ZB_TIMER_CANCEL(&native_timer);
    native_timer = TL_ZB_TIMER_SCHEDULE(timer_fired, 0, delay);
    last_timer_attempt = native_timer ? 0xffffffffu : second;
}

static void from_short(nc_addr_t *a, u16 short_addr, u8 ep) {
    memset(a, 0, sizeof(*a)); memset(a->id, 0xff, 8);
    zb_address_ieee_by_short(short_addr, a->id); a->ep = ep;
}
void nc_aps_confirm(void *arg) {
    apsdeDataConf_t *c = arg;
    nc_addr_t a;
    if (c->srcEndpoint != SENSOR_DEVICE_ENDPOINT || (c->clusterId != 6 && c->clusterId != 8)) return;
    memset(&a, 0, sizeof(a));
    if (c->dstAddrMode == APS_LONG_DSTADDR_WITHEP) {
        memcpy(a.id, c->dstAddr.addr_long, 8); a.ep = c->dstEndpoint;
    } else if (c->dstAddrMode == APS_SHORT_DSTADDR_WITHEP) {
        from_short(&a, c->dstAddr.addr_short, c->dstEndpoint);
    } else if (c->dstAddrMode == APS_SHORT_GROUPADDR_NOEP) {
        a.group = 1; nc_put16(a.id, c->dstAddr.addr_short);
    } else return;
    nc_confirm(&a, c->apsCnt, c->clusterId, c->status);
}

void nc_rx(void *message) {
    apsdeDataInd_t *m = message;
    nc_addr_t source;
    if (m->indInfo.cluster_id == NC_CLUSTER) nc_diagnostics();
    if (m->indInfo.cluster_id == 10 ||
        ((m->indInfo.cluster_id == 6 || m->indInfo.cluster_id == 8) &&
         m->asduLen >= 3 && !(m->asdu[0] & 3) && m->asdu[2] == 0x0b)) {
        if (m->asduLen >= 3 && !(m->asdu[0] & 0xe7) && (m->asdu[0] & 8) &&
            m->indInfo.profile_id == HA_PROFILE_ID && UNICAST_MSG(m)) {
            if (m->indInfo.cluster_id == 10 && m->asdu[2] == 1 &&
                m->indInfo.src_short_addr == 0 && m->indInfo.src_ep == 1)
                nc_time_response(m->asdu[1], m->asdu + 3, m->asduLen - 3);
            else if (m->indInfo.cluster_id != 10 && m->asduLen == 5) {
                from_short(&source, m->indInfo.src_short_addr, m->indInfo.src_ep);
                nc_rejection(&source, m->asdu[1], m->indInfo.cluster_id, m->asdu[3], m->asdu[4]);
            }
        }
        /* Consume before the SDK's unbounded Read Response parser. */
        ev_buf_free(message); return;
    }
    zcl_rx_handler(message);
}

static status_t configure(zclIncoming_t *m) {
    if (m->hdr.cmd != 0 || m->hdr.frmCtrl.bf.dir || m->hdr.manufCode != NC_MANUFACTURER)
        return ZCL_STA_UNSUP_MANU_CLUSTER_COMMAND;
    if (!UNICAST_MSG(m->msg) || m->msg->indInfo.src_short_addr != 0 ||
        !(m->msg->indInfo.security_status & SECURITY_IN_NWKLAYER)) return NC_NOT_AUTHORIZED;
    return nc_configure(m->pData, m->dataLen);
}
const zclAttrInfo_t nc_attrs[2] = {
    {0, ZCL_DATA_TYPE_OCTET_STR, ACCESS_CONTROL_READ, nc.cfg_attr},
    {1, ZCL_DATA_TYPE_OCTET_STR, ACCESS_CONTROL_READ, nc.diag_attr},
};
status_t nc_cluster_register(u8 ep, u16 manufacturer, u8 count,
                            const zclAttrInfo_t attrs[], cluster_forAppCb_t callback) {
    (void)callback;
    return zcl_registerCluster(ep, NC_CLUSTER, manufacturer, count, attrs, configure, 0);
}
