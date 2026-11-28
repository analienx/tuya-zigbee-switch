$(OUT_PATH)/src/nc_%.o: GCC_FLAGS += -Os -fno-inline-small-functions
INCLUDE_PATHS += -I$(SDK_PATH)/zigbee/nwk/includes
