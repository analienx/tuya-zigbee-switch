# The pinned TC32 compiler can emit narrow relative switch tables at -Os
# and still dispatch using a 32-bit absolute PC load. Avoid those tables in
# the affected application and source-built SDK objects.
$(OUT_PATH)/src/zb_appCb.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/bdb/bdb.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/zcl.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/zcl_reporting.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/ota_upgrading/zcl_ota.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/ota/ota.o: GCC_FLAGS += -fno-jump-tables

# Recover the switch fix's space in source-built software helpers. Leave the
# hardware timers, ADC acquisition, power management, RF and flash I/O alone.
$(OUT_PATH)/proj/os/ev_timer.o: GCC_FLAGS += -Os -fno-inline-small-functions -fno-jump-tables
$(OUT_PATH)/proj/os/ev_poll.o: GCC_FLAGS += -Os -fno-inline-small-functions -fno-jump-tables
$(OUT_PATH)/proj/common/string.o: GCC_FLAGS += -Os -fno-inline-small-functions -fno-jump-tables
$(OUT_PATH)/proj/common/list.o: GCC_FLAGS += -Os -fno-inline-small-functions -fno-jump-tables
$(OUT_PATH)/zigbee/aps/aps_group.o: GCC_FLAGS += -Os -fno-inline-small-functions -fno-jump-tables
