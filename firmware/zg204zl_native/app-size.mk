# Application protocol/attribute callbacks, not sensor acquisition, RF, CPU,
# calibration, PM or SDK driver objects. Keep the experiment separately visible.
$(OUT_PATH)/src/app_EpCfg.o: GCC_FLAGS += -Os
$(OUT_PATH)/src/zcl_appCb.o: GCC_FLAGS += -Os
$(OUT_PATH)/src/zb_appCb.o: GCC_FLAGS += -Os
$(OUT_PATH)/src/app_main.o: GCC_FLAGS += -Os
