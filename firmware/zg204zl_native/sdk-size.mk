# Only source-built application protocol code. Prebuilt stack/driver libraries,
# PM, ADC, RF and CPU compiler flags remain unchanged.
$(OUT_PATH)/zigbee/zcl/%.o: GCC_FLAGS += -Os
$(OUT_PATH)/zigbee/bdb/%.o: GCC_FLAGS += -Os
$(OUT_PATH)/zigbee/ota/%.o: GCC_FLAGS += -Os
