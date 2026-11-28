# The upstream explicitly forces small-function inlining even with -Os.
# Compare a compact protocol-only profile. No RF/CPU/ADC/PM/driver objects.
$(OUT_PATH)/zigbee/zcl/%.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/zigbee/bdb/%.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/zigbee/ota/%.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/zigbee/af/%.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/zigbee/zdo/%.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/src/app_EpCfg.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/src/zcl_appCb.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/src/zb_appCb.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/src/app_main.o: GCC_FLAGS += -fno-inline-small-functions
$(OUT_PATH)/src/reporting.o: GCC_FLAGS += -Os -fno-inline-small-functions
