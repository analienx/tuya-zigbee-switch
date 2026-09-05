#ifndef _CONFIG_NV_H_
#define _CONFIG_NV_H_

#include <stdbool.h>
#include <stdint.h>

// Parser storage capacities. Keep the validation preflight and the concrete
// parser arrays on the same constants so a configuration is either accepted
// in full or rejected before any GPIO/Zigbee side effect occurs.
#define DEVICE_CONFIG_MAX_LEDS             5
#define DEVICE_CONFIG_MAX_BUTTONS          11
#define DEVICE_CONFIG_MAX_RELAYS           10
#define DEVICE_CONFIG_MAX_ENDPOINTS        10
#define DEVICE_CONFIG_CLUSTER_POOL_SIZE    48

// Following structure (2 byte length, data follows) is ZCL LONG_STRING format.
// This way it allows us to use it directly inside Basic cluster
typedef struct {
    uint16_t size;
    uint8_t  data[128];
} device_config_str_t;

extern device_config_str_t device_config_str;

void device_config_write_to_nv();
void device_config_remove_from_nv();
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

// Called after the raw NVM read and before parse_config mutates GPIOs or Zigbee
// structures. If the stored config is structurally/resource unsafe, use the
// compiled board default for this boot (without overwriting the suspect NVM).
bool device_config_prepare_for_parse(void);

void handle_version_changes();

#endif
