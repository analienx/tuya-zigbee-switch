#include "hal/zigbee.h"
#include "app.h"
#include "hal/timer.h"

#include "app/framework/include/af.h"
#include "app/framework/plugin/ota-client/ota-client.h"
#include "network-steering.h"
#include "app/util/zigbee-framework/zigbee-device-common.h"
#include "telink/hal/router_recovery_policy.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MAX_CLUSTERS    32
#define MAX_ATTRS       128

sl_zigbee_af_endpoint_type_t      endpoint_type_buffer[ZCL_FIXED_ENDPOINT_COUNT];
sl_zigbee_af_cluster_t            clusters_buffer[MAX_CLUSTERS];
sl_zigbee_af_attribute_metadata_t attributes_buffer[MAX_ATTRS];

hal_zigbee_endpoint *hal_endpoints;
uint8_t hal_endpoints_cnt;
static hal_zcl_activity_callback_t zcl_activity_callback = NULL;

static void notify_zcl_activity(void) {
    if (zcl_activity_callback != NULL) {
        zcl_activity_callback();
    }
}

hal_zigbee_cluster *find_hal_cluster(uint8_t endpoint,
                                     sl_zigbee_af_cluster_id_t clusterId) {
    return hal_zigbee_find_cluster(hal_endpoints, hal_endpoints_cnt, endpoint,
                                   clusterId);
}

hal_zigbee_attribute *find_hal_attr(uint8_t endpoint,
                                    sl_zigbee_af_cluster_id_t clusterId,
                                    sl_zigbee_af_attribute_id_t attributeId) {
    return hal_zigbee_find_attribute(hal_endpoints, hal_endpoints_cnt, endpoint,
                                     clusterId, attributeId);
}

static uint32_t on_command_callback(sl_service_opcode_t opcode,
                                    sl_service_function_context_t *context) {
    assert(opcode == SL_SERVICE_FUNCTION_TYPE_ZCL_COMMAND);

    sl_zigbee_af_cluster_command_t *cmd =
        (sl_zigbee_af_cluster_command_t *)context->data;
    hal_zigbee_cluster *hal_cluster = find_hal_cluster(
        cmd->apsFrame->destinationEndpoint, cmd->apsFrame->clusterId);
    if (hal_cluster == NULL || hal_cluster->cmd_callback == NULL)
        return SL_ZIGBEE_ZCL_STATUS_UNSUP_COMMAND;

    uint8_t *payload     = NULL;
    uint16_t payload_len = 0;
    if (cmd->bufLen > cmd->payloadStartIndex) {
        payload     = cmd->buffer + cmd->payloadStartIndex;
        payload_len = cmd->bufLen - cmd->payloadStartIndex;
    }

    hal_zigbee_cmd_result_t res = hal_cluster->cmd_callback(
        cmd->apsFrame->destinationEndpoint, cmd->apsFrame->clusterId,
        cmd->commandId, payload, payload_len);
    if (res == HAL_ZIGBEE_CMD_SKIPPED) {
        return SL_ZIGBEE_ZCL_STATUS_UNSUP_COMMAND;
    }
    sl_zigbee_af_status_t status = SL_ZIGBEE_ZCL_STATUS_SUCCESS;
    if (res == HAL_ZIGBEE_INVALID_VALUE) {
        status = SL_ZIGBEE_ZCL_STATUS_INVALID_VALUE;
    } else if (res == HAL_ZIGBEE_MALFORMED_COMMAND) {
        status = SL_ZIGBEE_ZCL_STATUS_MALFORMED_COMMAND;
    } else if (res == HAL_ZIGBEE_ACTION_DENIED) {
        status = SL_ZIGBEE_ZCL_STATUS_ACTION_DENIED;
    }
    sl_zigbee_af_send_immediate_default_response(status);
    return status;
}

bool sl_zigbee_af_pre_command_received_cb(sl_zigbee_af_cluster_command_t *cmd) {
    if (cmd != NULL && cmd->apsFrame != NULL &&
        cmd->apsFrame->clusterId == 0x0019u &&
        cmd->commandId == 0x05u) {
        /* An OTA Image Block Response is proof of an active transfer.
         * Hold route-recovery scans off for this entire boot, including
         * paused/resumed transfers with missing completion callbacks. */
        silabs_router_ota_transfer_seen = true;
    }
    silabs_router_last_inbound_ms = hal_millis();
    notify_zcl_activity();
    return false;
}

void hal_zigbee_init(hal_zigbee_endpoint *endpoints, uint8_t endpoints_cnt) {
    hal_endpoints     = endpoints;
    hal_endpoints_cnt = endpoints_cnt;

    for (int i = 0; i < ZCL_FIXED_ENDPOINT_COUNT; i++) {
        sl_zigbee_af_endpoint_enable_disable(sli_zigbee_af_endpoints[i].endpoint,
                                             false);
    }

    // Avoid settings more endpoints then device supports
    endpoints_cnt = endpoints_cnt <= ZCL_FIXED_ENDPOINT_COUNT
                      ? endpoints_cnt
                      : ZCL_FIXED_ENDPOINT_COUNT;

    sl_zigbee_af_endpoint_type_t *     endpoint_type_ptr = endpoint_type_buffer;
    sl_zigbee_af_cluster_t *           cluster_ptr       = clusters_buffer;
    sl_zigbee_af_attribute_metadata_t *attr_ptr          = attributes_buffer;

    for (int i = 0; i < endpoints_cnt; i++) {
        sli_zigbee_af_endpoints[i].endpoint      = endpoints[i].endpoint;
        sli_zigbee_af_endpoints[i].profileId     = endpoints[i].profile_id;
        sli_zigbee_af_endpoints[i].deviceId      = endpoints[i].device_id;
        sli_zigbee_af_endpoints[i].deviceVersion = endpoints[i].device_version;
        sli_zigbee_af_endpoints[i].endpointType  = endpoint_type_ptr;

        endpoint_type_ptr->clusterCount = endpoints[i].cluster_count;
        endpoint_type_ptr->cluster      = cluster_ptr;
        endpoint_type_ptr->endpointSize = 0;

        hal_zigbee_cluster *clusters = endpoints[i].clusters;

        for (int j = 0; j < endpoints[i].cluster_count; j++) {
            cluster_ptr->clusterId      = clusters[j].cluster_id;
            cluster_ptr->clusterSize    = 0;
            cluster_ptr->attributeCount = clusters[j].attribute_count;
            cluster_ptr->mask           =
                clusters[j].is_server ? CLUSTER_MASK_SERVER : CLUSTER_MASK_CLIENT;
            cluster_ptr->attributes = attr_ptr;

            hal_zigbee_attribute *attributes = clusters[j].attributes;

            for (int l = 0; l < clusters[j].attribute_count; l++) {
                attr_ptr->attributeId   = attributes[l].attribute_id;
                attr_ptr->attributeType = attributes[l].data_type_id;
                attr_ptr->size          = attributes[l].size;
                attr_ptr->mask          = ATTRIBUTE_MASK_EXTERNAL_STORAGE;
                if (attributes[l].flag == ATTR_WRITABLE) {
                    attr_ptr->mask |= ATTRIBUTE_MASK_WRITABLE;
                }
                attr_ptr++;
            }

            if (clusters[j].cmd_callback) {
                sl_zigbee_subscribe_to_zcl_commands(
                    clusters[j].cluster_id, 0xFFFF,
                    clusters[j].is_server ? ZCL_DIRECTION_CLIENT_TO_SERVER
                                  : ZCL_DIRECTION_SERVER_TO_CLIENT,
                    on_command_callback);
            }
            cluster_ptr++;
        }

        sl_zigbee_af_endpoint_enable_disable(sli_zigbee_af_endpoints[i].endpoint,
                                             true);
        endpoint_type_ptr++;
    }
}

void hal_zigbee_notify_attribute_changed(uint8_t endpoint, uint16_t cluster_id,
                                         uint16_t attribute_id) {
    hal_zigbee_cluster *  cluster = find_hal_cluster(endpoint, cluster_id);
    hal_zigbee_attribute *attr    =
        find_hal_attr(endpoint, cluster_id, attribute_id);

    if (attr == NULL) {
        return;
    }
    sl_zigbee_af_reporting_attribute_change_cb(
        endpoint, cluster_id, attribute_id,
        cluster->is_server ? CLUSTER_MASK_SERVER : CLUSTER_MASK_CLIENT, 0,
        attr->data_type_id, attr->value);
}

static hal_attribute_change_callback_t attribute_change_callback = NULL;

void hal_zigbee_register_on_attribute_change_callback(
    hal_attribute_change_callback_t callback) {
    attribute_change_callback = callback;
}

void hal_zigbee_register_on_zcl_activity_callback(
    hal_zcl_activity_callback_t callback) {
    zcl_activity_callback = callback;
}

sl_zigbee_af_status_t sl_zigbee_af_external_attribute_read_cb(
    uint8_t endpoint, sl_zigbee_af_cluster_id_t clusterId,
    sl_zigbee_af_attribute_metadata_t *attributeMetadata,
    uint16_t manufacturerCode, uint8_t *buffer, uint16_t maxReadLength) {
    hal_zigbee_attribute *attr =
        find_hal_attr(endpoint, clusterId, attributeMetadata->attributeId);

    if (attr == NULL) {
        return SL_ZIGBEE_ZCL_STATUS_UNSUPPORTED_ATTRIBUTE;
    }
    if (maxReadLength < attr->size) {
        return SL_ZIGBEE_ZCL_STATUS_INSUFFICIENT_SPACE;
    }

    memmove(buffer, attr->value, attr->size);

    return SL_ZIGBEE_ZCL_STATUS_SUCCESS;
}

sl_zigbee_af_status_t sl_zigbee_af_external_attribute_write_cb(
    uint8_t endpoint, sl_zigbee_af_cluster_id_t clusterId,
    sl_zigbee_af_attribute_metadata_t *attributeMetadata,
    uint16_t manufacturerCode, uint8_t *buffer) {
    hal_zigbee_attribute *attr =
        find_hal_attr(endpoint, clusterId, attributeMetadata->attributeId);

    if (attr == NULL) {
        return SL_ZIGBEE_ZCL_STATUS_UNSUPPORTED_ATTRIBUTE;
    }
    memmove(attr->value, buffer, attr->size);

    if (attribute_change_callback != NULL) {
        attribute_change_callback(endpoint, clusterId, attr->attribute_id);
    }

    return SL_ZIGBEE_ZCL_STATUS_SUCCESS;
}

static bool     network_steering_in_progress = false;
static bool     network_steering_retry_pending;
static uint32_t network_steering_last_attempt_ms;
#define NETWORK_STEERING_RETRY_MS    10000u

/* A full Silicon Labs Router can remain JOINED while coordinator routing is
 * broken. This probe is ZDO unicast with native NWK route discovery. Never
 * trigger an active scan from a single route miss. */
#define SILABS_ROUTER_ZDO_INTERVAL_MS (6u * 60u * 1000u)
#define SILABS_ROUTER_ZDO_TIMEOUT_MS  (30u * 1000u)
#define SILABS_ROUTER_QUIET_MS        (20u * 60u * 1000u)
#define SILABS_ROUTER_ACTIVE_HOLD_MS  (60u * 60u * 1000u)
#define SILABS_ROUTER_NEIGHBOR_MS     (15u * 60u * 1000u)
#define SILABS_ROUTER_REJOIN_GAP_MS   (30u * 60u * 1000u)
#define SILABS_ROUTER_ORPHAN_MS       (10u * 60u * 1000u)
static bool     silabs_router_probe_initialized;
static bool     silabs_router_probe_pending;
static bool     silabs_router_rejoin_pending;
static bool     silabs_router_rejoin_orphaned;
static bool     silabs_router_rejoin_attempted;
static bool     silabs_router_ota_transfer_seen;
static uint8_t  silabs_router_probe_sequence;
static uint8_t  silabs_router_failed_probes;
static uint32_t silabs_router_last_probe_ms;
static uint32_t silabs_router_last_good_ms;
static uint32_t silabs_router_last_inbound_ms;
static uint32_t silabs_router_last_rejoin_ms;

void silabs_router_recovery_tick(void) {
    sl_zigbee_node_type_t role;
    if (sl_zigbee_af_get_node_type(&role) != SL_STATUS_OK ||
        role != SL_ZIGBEE_ROUTER ||
        sl_zigbee_af_network_state() != SL_ZIGBEE_JOINED_NETWORK ||
        silabs_router_ota_transfer_seen || network_steering_in_progress) {
        silabs_router_probe_initialized = false;
        silabs_router_probe_pending = false;
        return;
    }

    uint32_t now = hal_millis();
    /* Deterministically stagger scans; fixed, stable EUI avoids synchronized
     * retries in a large Router mesh. */
    uint8_t eui[8] = {0};
    sl_zigbee_af_get_eui64(eui);
    uint32_t seed = ((uint32_t)eui[1] << 16) |
                    ((uint32_t)eui[4] << 8) | eui[7];
    uint32_t probe_stagger = seed % 120000u;
    uint32_t rejoin_stagger = seed % (15u * 60u * 1000u);
    if (!silabs_router_probe_initialized) {
        silabs_router_probe_initialized = true;
        silabs_router_last_probe_ms = now;
        silabs_router_last_good_ms = now;
        silabs_router_last_inbound_ms = now;
        return;
    }
    if (silabs_router_rejoin_pending) {
        if ((uint32_t)(now - silabs_router_last_rejoin_ms) <
            SILABS_ROUTER_ORPHAN_MS)
            return;
        silabs_router_rejoin_pending = false;
        silabs_router_rejoin_orphaned = true;
        printf("Silabs Router: rejoin callback missing, refusing overlap\n");
    }
    if (silabs_router_probe_pending &&
        (uint32_t)(now - silabs_router_last_probe_ms) >=
            SILABS_ROUTER_ZDO_TIMEOUT_MS) {
        silabs_router_probe_pending = false;
        if (silabs_router_failed_probes < UINT8_MAX)
            silabs_router_failed_probes++;
    }
    bool unreachable = bseed_router_timeout_evidence(
        silabs_router_failed_probes, 4u, now,
        silabs_router_last_good_ms, SILABS_ROUTER_QUIET_MS);
    if (bseed_router_rejoin_eligible(
            unreachable, silabs_router_rejoin_orphaned, now,
            silabs_router_last_good_ms, silabs_router_last_inbound_ms,
            SILABS_ROUTER_QUIET_MS, SILABS_ROUTER_ACTIVE_HOLD_MS,
            SILABS_ROUTER_NEIGHBOR_MS, rejoin_stagger) &&
        (!silabs_router_rejoin_attempted ||
         (uint32_t)(now - silabs_router_last_rejoin_ms) >=
            SILABS_ROUTER_REJOIN_GAP_MS)) {
        silabs_router_rejoin_attempted = true;
        silabs_router_last_rejoin_ms = now;
        /* ENCRYPTED, preserve network credentials, channel-mask=current.
         * No insecure fallback, BDB steering, permit-join or factory reset. */
        sl_status_t st = sl_zigbee_find_and_rejoin_network(
            true, 0, SL_ZIGBEE_REJOIN_DUE_TO_APP_EVENT_1,
            SL_ZIGBEE_DEVICE_TYPE_UNCHANGED);
        if (st == SL_STATUS_OK) {
            silabs_router_rejoin_pending = true;
        } else {
            printf("Silabs Router: encrypted rejoin rejected 0x%lx\n",
                   (unsigned long)st);
        }
        return;
    }
    if (!silabs_router_probe_pending &&
        (uint32_t)(now - silabs_router_last_probe_ms) >=
            SILABS_ROUTER_ZDO_INTERVAL_MS + probe_stagger) {
        silabs_router_last_probe_ms = now;
        sl_status_t st = sl_zigbee_node_descriptor_request(
            0x0000u, SL_ZIGBEE_APS_OPTION_RETRY |
                     SL_ZIGBEE_APS_OPTION_ENABLE_ROUTE_DISCOVERY);
        if (st == SL_STATUS_OK) {
            silabs_router_probe_sequence =
                sl_zigbee_get_last_stack_zig_dev_request_sequence();
            silabs_router_probe_pending = true;
        } else {
            /* Local queue congestion is not evidence that 0x0000 is down. */
            printf("Silabs Router: ZDO enqueue rejected 0x%lx\n",
                   (unsigned long)st);
        }
    }
}

/* SDK permits inspecting a ZDO response without consuming it; leave the
 * normal framework response handling intact. */
bool sl_zigbee_af_pre_zdo_message_received_cb(
    sl_802154_short_addr_t source, sl_zigbee_aps_frame_t *aps,
    int8u *message, int16u length) {
    if (silabs_router_probe_pending && source == 0x0000u &&
        aps != NULL && aps->clusterId == 0x8002u &&
        message != NULL && length >= 4 &&
        message[0] == silabs_router_probe_sequence &&
        message[1] == 0u && message[2] == 0u && message[3] == 0u) {
        uint32_t now = hal_millis();
        silabs_router_probe_pending = false;
        silabs_router_failed_probes = 0;
        silabs_router_last_good_ms = now;
    }
    return false;
}

hal_zigbee_network_status_t hal_zigbee_get_network_status() {
    sl_zigbee_network_status_t ns = sl_zigbee_af_network_state();

    if (ns == SL_ZIGBEE_JOINED_NETWORK) {
        return HAL_ZIGBEE_NETWORK_JOINED;
    } else if (ns == SL_ZIGBEE_JOINING_NETWORK || network_steering_in_progress) {
        return HAL_ZIGBEE_NETWORK_JOINING;
    } else {
        return HAL_ZIGBEE_NETWORK_NOT_JOINED;
    }
}

static hal_network_status_change_callback_t network_status_change_callback =
    NULL;

static void notify_network_status_change(void) {
    if (network_status_change_callback != NULL) {
        network_status_change_callback(hal_zigbee_get_network_status());
    }
}

void hal_register_on_network_status_change_callback(
    hal_network_status_change_callback_t callback) {
    network_status_change_callback = callback;
}

void sl_zigbee_af_stack_status_cb(sl_status_t status) {
    /* A successful secure rejoin can occur without the application seeing a
     * NOT_JOINED interval; explicitly rearm the per-epoch Device Announce. */
    if (status == SL_STATUS_NETWORK_UP) {
        silabs_router_rejoin_pending = false;
        silabs_router_rejoin_orphaned = false;
        silabs_router_failed_probes = 0;
        silabs_router_probe_initialized = false;
        app_rearm_network_announcement();
    } else if (status == SL_STATUS_NETWORK_DOWN) {
        silabs_router_rejoin_pending = false;
        silabs_router_probe_initialized = false;
    }
    notify_network_status_change();
}

void hal_zigbee_leave_network() {
    sl_zigbee_leave_network(SL_ZIGBEE_LEAVE_NWK_WITH_NO_OPTION);
}

void hal_zigbee_start_network_steering() {
    /* On a joined Router, network steering opens the network for joining;
     * it is NOT a connectivity probe or route repair operation. */
    if (sl_zigbee_af_network_state() == SL_ZIGBEE_JOINED_NETWORK ||
        network_steering_in_progress) {
        return;
    }

    uint32_t now = hal_millis();
    if (network_steering_retry_pending &&
        (uint32_t)(now - network_steering_last_attempt_ms) <
        NETWORK_STEERING_RETRY_MS) {
        return;
    }

    /* Silicon Labs explicitly documents synchronous rejection (e.g. scan
     * queue full, invalid state). Never latch JOINING on a rejected start. */
    network_steering_last_attempt_ms = now;
    network_steering_retry_pending   = true;
    network_steering_in_progress     = true;
    sl_status_t status = sl_zigbee_af_network_steering_start();
    if (status != SL_STATUS_OK) {
        network_steering_in_progress = false;
        printf("Silabs network steering start rejected: 0x%lx\n",
               (unsigned long)status);
    }
}

// Network steering complete callback
void sl_zigbee_af_network_steering_complete_cb(sl_status_t status,
                                               uint8_t totalBeacons,
                                               uint8_t joinAttempts,
                                               uint8_t finalState) {
    (void)status;
    (void)totalBeacons;
    (void)joinAttempts;
    (void)finalState;
    network_steering_in_progress = false;

    /* Even if steering reports success, don't immediately start it again
     * when the stack has not yet transitioned to JOINED. */
    network_steering_retry_pending =
        (status != SL_STATUS_OK ||
         sl_zigbee_af_network_state() != SL_ZIGBEE_JOINED_NETWORK);
    network_steering_last_attempt_ms = hal_millis();

    notify_network_status_change();
}

static uint8_t make_frame_control(const hal_zigbee_cmd *c) {
    uint8_t fc = 0;

    if (c->cluster_specific)
        fc |= ZCL_CLUSTER_SPECIFIC_COMMAND;
    if (c->direction == HAL_ZIGBEE_DIR_SERVER_TO_CLIENT)
        fc |= ZCL_FRAME_CONTROL_SERVER_TO_CLIENT;
    if (c->disable_default_rsp)
        fc |= ZCL_DISABLE_DEFAULT_RESPONSE_MASK;
    if (c->manufacturer_code)
        fc |= ZCL_MANUFACTURER_SPECIFIC_MASK;
    return fc;
}

static void fill_cmd(const hal_zigbee_cmd *c) {
    sl_zigbee_set_manufacturer_code(c->manufacturer_code);
    uint8_t fc = make_frame_control(c);

    if (c->payload_len == 0 || c->payload == NULL) {
        sl_zigbee_af_fill_external_buffer(fc, c->cluster_id, c->command_id, "");
    } else {
        sl_zigbee_af_fill_external_buffer(fc, c->cluster_id, c->command_id, "b",
                                          c->payload, c->payload_len);
    }
}

bool hal_zigbee_has_binding(uint8_t endpoint, uint16_t cluster_id) {
    uint8_t count = sl_zigbee_get_binding_table_size();

    for (uint8_t i = 0; i < count; i++) {
        sl_zigbee_binding_table_entry_t entry;
        if (sl_zigbee_get_binding(i, &entry) == SL_STATUS_OK &&
            entry.type != SL_ZIGBEE_UNUSED_BINDING &&
            entry.local == endpoint && entry.clusterId == cluster_id) {
            return true;
        }
    }
    return false;
}

hal_zigbee_status_t hal_zigbee_send_cmd_to_bindings(const hal_zigbee_cmd *cmd) {
    if (!cmd)
        return HAL_ZIGBEE_ERR_BAD_ARG;

    if (sl_zigbee_af_network_state() != SL_ZIGBEE_JOINED_NETWORK)
        return HAL_ZIGBEE_ERR_NOT_JOINED;

    fill_cmd(cmd);

    sl_zigbee_af_set_command_endpoints(cmd->endpoint, cmd->endpoint);
    sl_status_t st_unicast   = sl_zigbee_af_send_command_unicast_to_bindings();
    sl_status_t st_multicast = sl_zigbee_af_send_command_multicast_to_bindings();

    return (st_unicast == SL_STATUS_OK || st_multicast == SL_STATUS_OK)
           ? HAL_ZIGBEE_OK : HAL_ZIGBEE_ERR_SEND_FAILED;
}

hal_zigbee_status_t
hal_zigbee_send_report_attr(uint8_t endpoint, uint16_t cluster_id,
                            uint16_t attr_id, uint8_t zcl_type_id,
                            const void *value, uint8_t value_len) {
    if (sl_zigbee_af_network_state() != SL_ZIGBEE_JOINED_NETWORK)
        return HAL_ZIGBEE_ERR_NOT_JOINED;

    uint8_t buf[2 + 1 + 8]; /* attrId(2) + type(1) + value */
    if (value_len > 8)
        return HAL_ZIGBEE_ERR_BAD_ARG;

    buf[0] = (uint8_t)(attr_id & 0xFF);
    buf[1] = (uint8_t)(attr_id >> 8);
    buf[2] = zcl_type_id;
    if (value_len)
        memmove(&buf[3], value, value_len);

    sl_status_t st =
        sl_zigbee_af_fill_command_global_server_to_client_report_attributes(
            cluster_id, buf, 3 + value_len);
    if (st != SL_STATUS_OK)
        return HAL_ZIGBEE_ERR_SEND_FAILED;

    sl_zigbee_af_set_command_endpoints(endpoint, endpoint);
    st = sl_zigbee_af_send_command_unicast_to_bindings();
    return (st == SL_STATUS_OK) ? HAL_ZIGBEE_OK : HAL_ZIGBEE_ERR_SEND_FAILED;
}

hal_zigbee_status_t hal_zigbee_send_announce(void) {
    if (sl_zigbee_send_device_announcement() != SL_STATUS_OK) {
        return HAL_ZIGBEE_ERR_SEND_FAILED;
    }
    return HAL_ZIGBEE_OK;
}

static bool hal_zigbee_poll_rate_supported(void) {
    sl_zigbee_node_type_t node_type = SL_ZIGBEE_UNKNOWN_DEVICE;

    if (sl_zigbee_af_get_node_type(&node_type) != SL_STATUS_OK) {
        return false;
    }

    return node_type == SL_ZIGBEE_END_DEVICE ||
           node_type == SL_ZIGBEE_SLEEPY_END_DEVICE ||
           node_type == SL_ZIGBEE_S2S_INITIATOR_DEVICE ||
           node_type == SL_ZIGBEE_S2S_TARGET_DEVICE;
}

void hal_zigbee_set_poll_rate_ms(uint32_t poll_rate_ms) {
    if (!hal_zigbee_poll_rate_supported()) {
        return;
    }
    // Only set the long poll interval, keep short poll managed by
    // SDK itself for Silabs
    sl_zigbee_af_set_long_poll_interval_ms_cb(poll_rate_ms);
}

uint32_t hal_zigbee_get_poll_rate_ms(void) {
    if (!hal_zigbee_poll_rate_supported()) {
        return 0;
    }

    return sl_zigbee_af_get_long_poll_interval_ms_cb();
}

void hal_zigbee_init_ota() {
}
