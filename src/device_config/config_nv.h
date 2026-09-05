#ifndef _CONFIG_NV_H_
#define _CONFIG_NV_H_

#include <stdbool.h>
#include <stdint.h>

// Parser storage capacities. These intentionally describe the concrete arrays
// in config_parser.c, not the wider NVM slot reservation. A candidate that
// exceeds the parser's current runtime capacity is rejected before parsing;
// increasing capacity is a separate compatibility decision.
#define DEVICE_CONFIG_MAX_LEDS              5
#define DEVICE_CONFIG_MAX_BUTTONS           11
#define DEVICE_CONFIG_MAX_RELAYS            10
#define DEVICE_CONFIG_MAX_SWITCH_CLUSTERS   4
#define DEVICE_CONFIG_MAX_RELAY_CLUSTERS    4
#define DEVICE_CONFIG_MAX_ENDPOINTS         10
#define DEVICE_CONFIG_CLUSTER_POOL_SIZE     32

// Following structure (2 byte length, data follows) is ZCL LONG_STRING format.
// This way it allows us to use it directly inside Basic cluster
typedef struct {
    uint16_t size;
    uint8_t  data[128];
} device_config_str_t;

extern device_config_str_t device_config_str;

void device_config_write_to_nv();
void device_config_remove_from_nv();

// Raw read is reserved for migration classification: it must not replace a
// foreign/corrupt stored value before the migration state machine has inspected
// it. Normal parser callers use device_config_read_from_nv(), which preflights
// resources and falls back in RAM before any hardware side effect.
void device_config_read_raw_from_nv();
void device_config_read_from_nv();

// Safe replacement used by the chunked Zigbee transport. The candidate is
// validated before this call; the NVM write is read back byte-for-byte before
// the in-memory config is changed.
bool device_config_replace_verified(const uint8_t *data, uint16_t size);

// Full validation used for commits. This includes structural/resource checks
// plus any board-specific guard selected by the release build.
bool device_config_is_valid(const uint8_t *data, uint16_t size);

// Generic parser-capacity preflight. It deliberately does not enforce a
// board-specific identity/topology policy; it only proves that parsing cannot
// overflow fixed storage or the endpoint/cluster pools.
bool device_config_resources_are_safe(const uint8_t *data, uint16_t size);

// If the stored config is structurally/resource unsafe, use the compiled board
// default for this boot (without overwriting the suspect NVM). This is called
// automatically by device_config_read_from_nv().
bool device_config_prepare_for_parse(void);

void handle_version_changes();

#endif
