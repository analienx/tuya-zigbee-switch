#include "zcl_write_string_gate.h"

#include "zigbee/consts.h"

/* ZCL foundation constants (ZCL-8 2.3.1; same values as SDK V3.7.2.0 zcl.h).
 * Kept local so this gate stays independent of the Telink SDK headers and
 * host-testable. */
#define ZWSG_FRAME_TYPE_MASK       0x03
#define ZWSG_FRAME_TYPE_PROFILE    0x00
#define ZWSG_FRAME_MANU_SPECIFIC   0x04
#define ZWSG_CMD_WRITE             0x02
#define ZWSG_CMD_WRITE_UNDIVIDED   0x03
#define ZWSG_CMD_WRITE_NO_RSP      0x05

/* Fixed payload sizes, mirroring SDK zcl_getDataTypeLen (fixed types only).
 * Variable-length and unknown types return 0: the gate cannot size them and
 * drops the message instead of walking blind. */
static uint16_t zwsg_fixed_data_len(uint8_t data_type) {
    switch (data_type) {
    case ZCL_DATA_TYPE_BOOLEAN:
    case ZCL_DATA_TYPE_DATA8:
    case ZCL_DATA_TYPE_BITMAP8:
    case ZCL_DATA_TYPE_UINT8:
    case ZCL_DATA_TYPE_INT8:
    case ZCL_DATA_TYPE_ENUM8:
        return 1;
    case ZCL_DATA_TYPE_DATA16:
    case ZCL_DATA_TYPE_BITMAP16:
    case ZCL_DATA_TYPE_UINT16:
    case ZCL_DATA_TYPE_INT16:
    case ZCL_DATA_TYPE_ENUM16:
    case ZCL_DATA_TYPE_SEMI_PREC:
    case ZCL_DATA_TYPE_CLUSTER_ID:
    case ZCL_DATA_TYPE_ATTR_ID:
        return 2;
    case ZCL_DATA_TYPE_DATA24:
    case ZCL_DATA_TYPE_BITMAP24:
    case ZCL_DATA_TYPE_UINT24:
    case ZCL_DATA_TYPE_INT24:
        return 3;
    case ZCL_DATA_TYPE_DATA32:
    case ZCL_DATA_TYPE_BITMAP32:
    case ZCL_DATA_TYPE_UINT32:
    case ZCL_DATA_TYPE_INT32:
    case ZCL_DATA_TYPE_SINGLE_PREC:
    case ZCL_DATA_TYPE_UTC:
    case ZCL_DATA_TYPE_DATE:
    case ZCL_DATA_TYPE_BAC_OID:
        return 4;
    case ZCL_DATA_TYPE_DATA40:
    case ZCL_DATA_TYPE_BITMAP40:
    case ZCL_DATA_TYPE_UINT40:
    case ZCL_DATA_TYPE_INT40:
        return 5;
    case ZCL_DATA_TYPE_DATA48:
    case ZCL_DATA_TYPE_BITMAP48:
    case ZCL_DATA_TYPE_UINT48:
    case ZCL_DATA_TYPE_INT48:
        return 6;
    case ZCL_DATA_TYPE_DATA56:
    case ZCL_DATA_TYPE_BITMAP56:
    case ZCL_DATA_TYPE_UINT56:
    case ZCL_DATA_TYPE_INT56:
        return 7;
    case ZCL_DATA_TYPE_DATA64:
    case ZCL_DATA_TYPE_BITMAP64:
    case ZCL_DATA_TYPE_UINT64:
    case ZCL_DATA_TYPE_INT64:
    case ZCL_DATA_TYPE_DOUBLE_PREC:
    case ZCL_DATA_TYPE_IEEE_ADDR:
        return 8;
    case ZCL_DATA_TYPE_128_BIT_SEC_KEY:
        return 16;
    default:
        return 0;
    }
}

static bool zwsg_is_short_string(uint8_t data_type) {
    return data_type == ZCL_DATA_TYPE_CHAR_STR ||
           data_type == ZCL_DATA_TYPE_OCTET_STR;
}

static bool zwsg_is_long_string(uint8_t data_type) {
    return data_type == ZCL_DATA_TYPE_LONG_CHAR_STR ||
           data_type == ZCL_DATA_TYPE_LONG_OCTET_STR;
}

/* Declared string capacity of the registered attribute: full storage extent
 * minus the ZCL length prefix. Returns false when the record cannot reach a
 * writable same-type string destination (SDK then rejects it without any
 * copy, so the gate has nothing to bound). */
static bool zwsg_string_capacity(const hal_zigbee_cluster *cluster,
                                 uint16_t attr_id, uint8_t data_type,
                                 uint16_t *capacity) {
    for (int i = 0; i < cluster->attribute_count; i++) {
        const hal_zigbee_attribute *attr = &cluster->attributes[i];
        if (attr->attribute_id != attr_id ||
            attr->data_type_id != data_type ||
            attr->flag != ATTR_WRITABLE) {
            continue;
        }
        uint16_t prefix = zwsg_is_long_string(data_type) ? 2u : 1u;
        uint16_t limit = attr->size;
        if (attr->write_max_size && attr->write_max_size < limit) {
            limit = attr->write_max_size;
        }
        *capacity = limit >= prefix ? limit - prefix : 0;
        return true;
    }
    return false;
}

bool zcl_write_string_gate_allows(const uint8_t *asdu, uint16_t asdu_len,
                                  uint8_t endpoint, uint16_t cluster_id,
                                  hal_zigbee_endpoint *endpoints,
                                  uint8_t endpoints_count) {
    if (asdu == NULL) {
        return false;
    }
    const hal_zigbee_cluster *cluster =
        hal_zigbee_find_cluster(endpoints, endpoints_count, endpoint,
                                cluster_id);
    if (asdu_len < 3) {
        return false;
    }
    if ((asdu[0] & ZWSG_FRAME_TYPE_MASK) != ZWSG_FRAME_TYPE_PROFILE) {
        return true;
    }
    uint16_t header = (asdu[0] & ZWSG_FRAME_MANU_SPECIFIC) ? 5u : 3u;
    if (asdu_len < header) {
        return false;
    }
    /* The command is the last header byte; records start immediately after
       the 3-byte (or manufacturer-specific 5-byte) header. */
    uint8_t cmd = asdu[header - 1u];
    if (cmd != ZWSG_CMD_WRITE && cmd != ZWSG_CMD_WRITE_UNDIVIDED &&
        cmd != ZWSG_CMD_WRITE_NO_RSP) {
        return true;
    }
    uint16_t offset    = header;
    uint16_t remaining = asdu_len - offset;
    while (remaining > 0) {
        if (remaining < 3u) {
            return false;
        }
        uint16_t attr_id   = asdu[offset] | ((uint16_t)asdu[offset + 1] << 8);
        uint8_t  data_type = asdu[offset + 2];
        uint16_t record;
        if (zwsg_is_short_string(data_type)) {
            if (remaining < 4u) {
                return false;
            }
            uint16_t declared = asdu[offset + 3];
            if (declared > remaining - 4u) {
                return false;
            }
            record = 4u + declared;
        } else if (zwsg_is_long_string(data_type)) {
            if (remaining < 5u) {
                return false;
            }
            uint16_t declared =
                asdu[offset + 3] | ((uint16_t)asdu[offset + 4] << 8);
            if (declared > remaining - 5u) {
                return false;
            }
            record = 5u + declared;
        } else {
            uint16_t fixed = zwsg_fixed_data_len(data_type);
            if (fixed == 0u || fixed > remaining - 3u) {
                return false;
            }
            record = 3u + fixed;
        }
        if (zwsg_is_short_string(data_type) ||
            zwsg_is_long_string(data_type)) {
            uint16_t prefix =
                zwsg_is_long_string(data_type) ? 2u : 1u;
            uint16_t declared = record - 3u - prefix;
            uint16_t capacity = 0u;
            if (cluster && zwsg_string_capacity(cluster, attr_id, data_type,
                                     &capacity) &&
                declared > capacity) {
                return false;
            }
        }
        offset += record;
        remaining -= record;
    }
    return true;
}
