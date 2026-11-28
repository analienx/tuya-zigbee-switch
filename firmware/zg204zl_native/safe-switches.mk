# The pinned TC32 compiler can emit narrow relative switch tables at -Os
# and still dispatch using a 32-bit absolute PC load. Avoid those tables in
# the affected application and source-built SDK objects.
$(OUT_PATH)/src/zb_appCb.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/bdb/bdb.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/zcl.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/zcl_reporting.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/zcl/ota_upgrading/zcl_ota.o: GCC_FLAGS += -fno-jump-tables
$(OUT_PATH)/zigbee/ota/ota.o: GCC_FLAGS += -fno-jump-tables
