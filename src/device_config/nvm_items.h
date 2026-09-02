#ifndef DEVICE_CONFIG_NVM_ITEMS_H_
#define DEVICE_CONFIG_NVM_ITEMS_H_

#define MAX_RELAYS                       5
#define MAX_SWITCHES                     5
#define MAX_COVER_SWITCHES               3
#define MAX_COVERS                       3

#define NV_ITEM_CURRENT_VERSION_IN_NV    1
#define NV_ITEM_DEVICE_CONFIG            2
#define NV_ITEM_BASIC_CLUSTER_DATA       3
// switch_idx and relay_idx below are zero indexes, e.g. first switch has
// switch_idx = 0
#define NV_ITEM_SWITCH_CLUSTER_DATA(switch_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + 1 + switch_idx)
#define NV_ITEM_RELAY_CLUSTER_DATA(relay_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + 1 + relay_idx)
#define NV_ITEM_COVER_SWITCH_CONFIG(cover_switch_idx) \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + MAX_RELAYS + 1 + cover_switch_idx)
#define NV_ITEM_COVER_CONFIG(cover_idx)                                                    \
        (NV_ITEM_BASIC_CLUSTER_DATA + MAX_SWITCHES + MAX_RELAYS + MAX_COVER_SWITCHES + 1 + \
         cover_idx)

// 3 + 5 (switches) + 5 (relays) + 3 (cover switches) + 3 (covers) = 19
// Adding room for future items, so starting from 32
#define NV_ITEM_DEVICE_TYPE                32

#define NV_ITEM_MULTI_PRESS_RESET_COUNT    33
#define NV_ITEM_POLL_CONTROL_CONFIG        34

// Physical relay mode is stored separately from the legacy relay-cluster
// structure so older NVM data remains binary-compatible. Five slots are
// reserved for relay indexes 0..4.
#define NV_ITEM_RELAY_PHYSICAL_MODE(relay_idx)    (35 + (relay_idx))

// Device-specific one-shot migration marker (device_migration.c). Written
// only after every other change the migration makes is complete, so a crash
// mid-migration simply re-runs it on the next boot.
#define NV_ITEM_MIGRATION_MARKER    40

// Locally tracked/intended On/Off state of each switch's binding target.
// Separate storage preserves the legacy relay-cluster NVM record ABI.
#define NV_ITEM_RELAY_BINDING_INTENT(relay_idx)    (41 + (relay_idx))

#endif /* DEVICE_CONFIG_NVM_ITEMS_H_ */
