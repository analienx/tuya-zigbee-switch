NC_PARTS := nc_cfg nc_ctrl nc_tx nc_time nc_rx nc_port_tlsr
OBJS := $(filter-out $(addsuffix .o,$(addprefix $(OUT_PATH)$(SRC_DIR)/,$(NC_PARTS))),$(OBJS))
OBJS += $(OUT_PATH)$(SRC_DIR)/nc_all.o
OBJ_LIST := $(OBJS) $(USER_OBJS)
$(ELF_FILE): $(OUT_PATH)$(SRC_DIR)/nc_all.o
$(OUT_PATH)$(SRC_DIR)/nc_all.o: GCC_FLAGS += -Os -fno-inline-small-functions
