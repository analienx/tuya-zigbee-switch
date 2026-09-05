#ifndef DEVICE_CONFIG_NVM_ITEMS_H_
#define DEVICE_CONFIG_NVM_ITEMS_H_

#define MAX_RELAYS                       5
#define MAX_SWITCHES                     5
#define MAX_COVER_SWITCHES               3
#define MAX_COVERS                       3

#define NV_ITEM_CURRENT_VERSION_IN_NV    1
#define NV_ITEM_DEVICE_CONFIG            2
#define NV_ITEM_BASIC_CLUSTER_DATA       3
#define NV_ITEM_SWITCH_CLUSTER_DATA(switch_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + 1 + switch_idx)
#define NV_ITEM_RELAY_CLUSTER_DATA(relay_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + 1 + relay_idx)
#define NV_ITEM_COVER_SWITCH_CONFIG(cover_switch_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + MAX_RELAYS + 1 + cover_switch_idx)
#define NV_ITEM_COVER_CONFIG(cover_idx)                                                    \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + MAX_RELAYS + MAX_COVER_SWITCHES + 1 + \
         cover_idx)

#define NV_ITEM_DEVICE_TYPE                32
#define NV_ITEM_MULTI_PRESS_RESET_COUNT    33
#define NV_ITEM_POLL_CONTROL_CONFIG        34

#define NV_ITEM_RELAY_PHYSICAL_MODE(relay_idx)    (35 + (relay_idx))
#define NV_ITEM_MIGRATION_MARKER                   40
#define NV_ITEM_RELAY_BINDING_INTENT(relay_idx)    (41 + (relay_idx))
#define NV_ITEM_SWITCH_BINDING_COMMAND_MODE(switch_idx)    (46 + (switch_idx))

/* Unified V8 metering NVM region.
 *
 * The historical metering fork used 40..44. Those IDs now collide with V7/V8
 * BSEED migration/binding state, so they MUST NOT be reused in the unified
 * firmware. Keep metering isolated at 64+; four endpoint accumulation slots
 * leave room for generic multi-endpoint devices while the BSEED socket uses EP1. */
#define NV_ITEM_ENERGY_ACCUMULATION(endpoint)    (64 + (endpoint) - 1)
#define NV_ITEM_ENERGY_CALIBRATION               68
#define NV_ITEM_OVERLOAD_CONFIG                  69

#endif /* DEVICE_CONFIG_NVM_ITEMS_H_ */
