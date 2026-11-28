# Compact the source-level NV record manager and pure software helpers.
# Physical flash operations, interrupts, RF, ADC, CPU and PM remain untouched.
$(OUT_PATH)/src/patch_z_sdk/drv_nv.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/proj/common/utility.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/proj/common/mempool.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/proj/os/ev_buffer.o: GCC_FLAGS += -Os -fno-inline-small-functions
$(OUT_PATH)/src/app_ui.o: GCC_FLAGS += -Os -fno-inline-small-functions
