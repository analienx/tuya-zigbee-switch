# Project configuration

PROJECT_ROOT := ../..
BUILD_DIR := $(PROJECT_ROOT)/build/telink
BIN_DIR := $(BUILD_DIR)/bin
TELINK_TOOLS_DIR := $(PROJECT_ROOT)/telink_tools
SRC_DIR := $(PROJECT_ROOT)/src

# Version and board configuration
VERSION_STR ?= "0.0.0"
FILE_VERSION ?= 0
NVM_MIGRATIONS_VERSION ?= $(shell cat ../../NVM_MIGRATIONS_VERSION 2>/dev/null || echo "1")

FIRMWARE_BASENAME ?= tlc_switch
DEVICE_TYPE ?= router
DEBUG ?= 0
CONFIG_STR ?= ja5osu5g;TS004F-Loginovo;IC4i;SC2u;BTB5;M;
MANUFACTURER_ID ?= 4417
IMAGE_TYPE ?= 43521
OTA_VERSION ?=
OTA_MANUFACTURER_ID ?= ${MANUFACTURER_ID}
OTA_IMAGE_TYPE ?= ${IMAGE_TYPE}

# Toolchain paths
SDK_PATH := $(TELINK_TOOLS_DIR)/sdk
TOOLCHAIN_PATH := $(TELINK_TOOLS_DIR)/toolchain/tc32
TC32_PATH := $(TOOLCHAIN_PATH)/bin

# Compiler and linker
CC := $(TC32_PATH)/tc32-elf-gcc
LD := $(TC32_PATH)/tc32-elf-ld
OBJCOPY := $(TC32_PATH)/tc32-elf-objcopy
OBJDUMP := $(TC32_PATH)/tc32-elf-objdump
SIZE := $(TC32_PATH)/tc32-elf-size

TLSRPGM_TTY ?= /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
TLSRPGM_TOOL := $(TELINK_TOOLS_DIR)/tlsrpgm/TlsrPgm.py

ifeq ($(DEVICE_TYPE), router)
	TEL_CHIP := -DMCU_CORE_8258=1 -DROUTER=1 -DMCU_STARTUP_8258=1
	LIBS := -ldrivers_8258 -lzb_router
endif

ifeq ($(DEVICE_TYPE), end_device)
	TEL_CHIP := -DMCU_CORE_8258=1 -DEND_DEVICE=1 -DMCU_STARTUP_8258=1 -DPM_ENABLE
	LIBS := -ldrivers_8258 -lzb_ed
endif

GCC_FLAGS := \
	-O2 \
	-ffunction-sections \
	-fdata-sections \
	-Wall \
	-fshort-enums \
	-finline-small-functions \
	-std=gnu99 \
	-funsigned-char \
	-fshort-wchar \
	-fms-extensions \
	-nostartfiles \
	-nostdlib \
	-MMD \
	-MP

LNK_FLAGS := --gc-sections -nostartfiles

ASM_FLAGS := \
	-fomit-frame-pointer \
	-fshort-enums \
	-Wall \
	-Wpacked \
	-Wcast-align \
	-fdata-sections \
	-ffunction-sections \
	-fno-use-cxa-atexit \
	-fno-rtti \
	-fno-threadsafe-statics

DEVICE_DEFS := \
    -DVERSION_STR="$(VERSION_STR)" \
	-DFILE_VERSION=$(FILE_VERSION) \
	-DNVM_MIGRATIONS_VERSION=$(NVM_MIGRATIONS_VERSION) \
	-DDEFAULT_CONFIG="$(CONFIG_STR)" \
	-DIMAGE_TYPE=$(IMAGE_TYPE) \
	-DHAL_TELINK \
	$(TEL_CHIP)

ifeq ($(DEBUG), 1)
	DEVICE_DEFS := $(DEVICE_DEFS) -DUART_PRINTF_MODE=1
endif

MIGRATION_DEFINES :=
ifneq ($(strip $(MIGRATION_FROM_CONFIG)),)
MIGRATION_DEFINES += \
	-DDEVICE_MIGRATION_FROM_CONFIG="$(MIGRATION_FROM_CONFIG)" \
	-DDEVICE_MIGRATION_TO_CONFIG="$(CONFIG_STR)"
ifeq ($(strip $(MIGRATION_REVERT)), 1)
MIGRATION_DEFINES += -DDEVICE_MIGRATION_REVERT
endif
endif

DEVICE_DEFS := $(DEVICE_DEFS) $(MIGRATION_DEFINES)

ifneq ($(strip $(DEVICE_CONFIG_GUARD)),)
DEVICE_DEFS := $(DEVICE_DEFS) -DDEVICE_CONFIG_GUARD_$(DEVICE_CONFIG_GUARD)
endif

# Unified PM target options. They are explicit build inputs rather than global
# firmware behavior, so TS0726 and every existing board remain unchanged.
BSEED_PM_B28WRPVX ?=
BSEED_PM_B28WRPVX_PROTECTION ?=
HLW8012_VOLTAGE_MULTIPLIER ?=
HLW8012_CURRENT_MULTIPLIER ?=
HLW8012_POWER_MULTIPLIER ?=

ifeq ($(strip $(BSEED_PM_B28WRPVX)),1)
DEVICE_DEFS += -DBSEED_PM_B28WRPVX
endif
ifeq ($(strip $(BSEED_PM_B28WRPVX_PROTECTION)),1)
DEVICE_DEFS += -DBSEED_PM_B28WRPVX_PROTECTION
endif
ifneq ($(strip $(HLW8012_VOLTAGE_MULTIPLIER)),)
DEVICE_DEFS += -DHLW8012_VOLTAGE_MULTIPLIER=$(HLW8012_VOLTAGE_MULTIPLIER)
endif
ifneq ($(strip $(HLW8012_CURRENT_MULTIPLIER)),)
DEVICE_DEFS += -DHLW8012_CURRENT_MULTIPLIER=$(HLW8012_CURRENT_MULTIPLIER)
endif
ifneq ($(strip $(HLW8012_POWER_MULTIPLIER)),)
DEVICE_DEFS += -DHLW8012_POWER_MULTIPLIER=$(HLW8012_POWER_MULTIPLIER)
endif

INCLUDE_PATHS := \
	-I. \
	-Iconfigs \
	-Ilibc_polyfills \
	-Icustom_zcl \
	-I$(SRC_DIR) \
	-I$(SDK_PATH)/platform \
	-I$(SDK_PATH)/platform/chip_8258 \
	-I$(SDK_PATH)/proj \
	-I$(SDK_PATH)/proj/common \
	-I$(SDK_PATH)/zigbee \
	-I$(SDK_PATH)/zigbee/af \
	-I$(SDK_PATH)/zigbee/include \
	-I$(SDK_PATH)/zigbee/bdb/includes \
	-I$(SDK_PATH)/zigbee/common/includes \
	-I$(SDK_PATH)/zigbee/ota \
	-I$(SDK_PATH)/zigbee/zbapi \
	-I$(SDK_PATH)/zigbee/zbhci \
	-I$(SDK_PATH)/zigbee/zcl \
	-I$(SDK_PATH)/zigbee/zdo

LIBRARY_PATHS := -L$(SDK_PATH)/zigbee/lib/tc32 -L$(SDK_PATH)/platform/lib

TELINK_SOURCES := \
	main.c \
	ota_reformating/ensure_ota_scheme.c \
	ota_reformating/ram_code_flash.c \
	hal/system.c \
	hal/timer.c \
	hal/tasks.c \
	hal/gpio.c \
	hal/gpio_counter.c \
	hal/gpio_interrupts.c \
	hal/nvm.c \
	hal/zigbee.c \
	hal/zigbee_network.c \
	hal/zigbee_zcl.c \
	hal/zigbee_ota.c \
	hal/adc.c \
	libc_polyfills/atoi.c \
	custom_zcl/zcl_multistate_input.c \
	custom_zcl/zcl_onoff_configuration.c \
	custom_zcl/zcl_cover_switch_config.c \
	patch_sdk/drv_nv.c

COMMON_SOURCES := \
	$(SRC_DIR)/app.c \
	$(SRC_DIR)/zigbee/poll_control_cluster.c \
	$(SRC_DIR)/base_components/button.c \
	$(SRC_DIR)/base_components/led.c \
	$(SRC_DIR)/base_components/network_indicator.c \
	$(SRC_DIR)/base_components/relay.c \
	$(SRC_DIR)/base_components/battery.c \
	$(SRC_DIR)/base_components/overload_protection.c \
	$(SRC_DIR)/base_components/energy_measurement/hlw8012.c \
	$(SRC_DIR)/device_config/config_nv.c \
	$(SRC_DIR)/device_config/device_params_nv.c \
	$(SRC_DIR)/device_config/reset.c \
	$(SRC_DIR)/device_config/config_parser.c \
	$(SRC_DIR)/device_config/nvm_migrations.c \
	$(SRC_DIR)/device_config/device_migration.c \
	$(SRC_DIR)/device_config/pm_legacy_migration.c \
	$(SRC_DIR)/zigbee/basic_cluster.c \
	$(SRC_DIR)/zigbee/general_commands.c \
	$(SRC_DIR)/zigbee/group_cluster.c \
	$(SRC_DIR)/zigbee/relay_cluster.c \
	$(SRC_DIR)/zigbee/switch_cluster.c \
	$(SRC_DIR)/zigbee/cover_switch_cluster.c \
	$(SRC_DIR)/zigbee/cover_cluster.c \
	$(SRC_DIR)/zigbee/battery_cluster.c \
	$(SRC_DIR)/zigbee/electrical_measurement_cluster.c \
	$(SRC_DIR)/zigbee/metering_cluster.c

ALL_TELINK_SOURCES := $(TELINK_SOURCES) $(COMMON_SOURCES)

include sdk.mk

TELINK_OBJS := $(TELINK_SOURCES:%.c=$(BUILD_DIR)/telink/%.o)
COMMON_OBJS := $(COMMON_SOURCES:$(SRC_DIR)/%.c=$(BUILD_DIR)/common/%.o)
ALL_APP_OBJS := $(TELINK_OBJS) $(COMMON_OBJS)

TELINK_DEPS := $(TELINK_SOURCES:%.c=$(BUILD_DIR)/telink/%.d)
COMMON_DEPS := $(COMMON_SOURCES:$(SRC_DIR)/%.c=$(BUILD_DIR)/common/%.d)
ALL_APP_DEPS := $(TELINK_DEPS) $(COMMON_DEPS)

ALL_OBJS := $(ALL_APP_OBJS) $(SDK_OBJS)

LINKER_SCRIPT := $(SDK_PATH)/platform/boot/8258/boot_8258.link

ELF_FILE := $(BUILD_DIR)/$(FIRMWARE_BASENAME).elf
LST_FILE := $(BUILD_DIR)/$(FIRMWARE_BASENAME).lst
BIN_FILE := $(BIN_DIR)/$(FIRMWARE_BASENAME).bin
OTA_FILE := $(BIN_DIR)/$(FIRMWARE_BASENAME).ota
SIZEDUMMY := $(BUILD_DIR)/sizedummy

all: $(BIN_FILE) $(LST_FILE)
	@echo "Build complete: $(BIN_FILE)"

$(ELF_FILE): $(ALL_APP_OBJS) sdk-build
	@echo "Linking $(ELF_FILE)"
	@$(LD) $(LNK_FLAGS) -T$(LINKER_SCRIPT) $(LIBRARY_PATHS) -o $@ $(ALL_APP_OBJS) $(SDK_OBJS) $(LIBS)

$(LST_FILE): $(ELF_FILE)
	@echo 'Invoking: TC32 Create Extended Listing'
	@$(OBJDUMP) -x -D -l -S $(ELF_FILE) > $(LST_FILE)

$(BIN_FILE): $(ELF_FILE)
	@echo "Creating binary $(BIN_FILE)"
	@mkdir -p $(dir $@)
	@$(OBJCOPY) -O binary $< $@
	@$(SIZE) $<

$(BUILD_DIR)/telink/%.o: %.c
	@echo "Compiling $<"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

$(BUILD_DIR)/common/%.o: $(SRC_DIR)/%.c
	@echo "Compiling $<"
	@mkdir -p $(dir $@)
	@$(CC) $(GCC_FLAGS) $(DEVICE_DEFS) $(INCLUDE_PATHS) -c -o $@ $<

build: all

ifdef OTA_VERSION
OTA_VERSION_ARG := --file-version $(OTA_VERSION)
endif

ota: $(BIN_FILE)
	@if [ -z "$(OTA_MANUFACTURER_ID)" ] || [ "$(OTA_MANUFACTURER_ID)" = "null" ] || \
      [ -z "$(OTA_IMAGE_TYPE)" ] || [ "$(OTA_IMAGE_TYPE)" = "null" ]; then \
        echo "Skipping OTA file generation: missing arguments"; \
        echo " "; \
    else \
	    echo 'Creating OTA file $(OTA_FILE)'; \
		echo ' '; \
	    mkdir -p $(dir $(OTA_FILE)); \
	    python3 $(SRC_DIR)/telink/make_ota.py create-ota \
	      --manufacturer-id $(OTA_MANUFACTURER_ID) \
	      --image-type $(OTA_IMAGE_TYPE) \
	      $(OTA_VERSION_ARG) \
	      $(BIN_FILE) $(OTA_FILE); \
	    echo ''; \
	fi

flash: $(BIN_FILE)
	@echo "Flashing $(BIN_FILE) to device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) -t25 -a 20 --mrst we 0 $(BIN_FILE)

restart:
	@echo "Restarting device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) -t25 -a 20 --mrst

wipe:
	@echo "Wiping device via $(TLSRPGM_TTY)"
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) -t25 -a 20 ea

flasher:
	@python3 $(TLSRPGM_TOOL) -p$(TLSRPGM_TTY) $(ARGS)

-include $(ALL_APP_DEPS)

clean: sdk-clean
	@echo "Cleaning Telink build artifacts..."
	@rm -rf $(BUILD_DIR)/telink
	@rm -rf $(BUILD_DIR)/common
	@rm -rf $(BIN_DIR)

tools/%:
	$(MAKE) -f tools.mk $*

help:
	@echo "Telink Build System"
	@echo "=================="
	@echo ""
	@echo "Tools Setup Targets:"
	@echo "  tools/all          - Download and install all Telink tools"
	@echo "  tools/status       - Show installation status and verify tools"
	@echo "  tools/verify       - Verify installed tools"
	@echo "  tools/help         - Show detailed tools help"
	@echo ""
	@echo "Quick Start:"
	@echo "  make build          - Check tools and build firmware"
	@echo "  make all            - Build firmware (assumes tools installed)"
	@echo ""
	@echo "Build Targets:"
	@echo "  build               - Build with tools verification"
	@echo "  all                 - Build firmware (ELF + BIN + LST files)"
	@echo "  ota                 - Create OTA update file from binary"
	@echo "  clean               - Clean build artifacts"
	@echo "  sdk-build           - Build SDK sources only"
	@echo "  sdk-clean           - Clean SDK build artifacts"
	@echo "  sdk-help            - Show SDK build help"
	@echo ""
	@echo "Hardware/Device Targets:"
	@echo "  flash               - Flash binary to device via programmer"
	@echo "  restart             - Restart device via programmer"
	@echo "  wipe                - Wipe device flash via programmer"
	@echo "  flasher             - Run programmer tool with custom ARGS"
	@echo ""
	@echo "Configuration Variables:"
	@echo "  DEVICE_TYPE         - router or end_device (default: $(DEVICE_TYPE))"
	@echo "  CONFIG_STR          - Device pin configuration string"
	@echo "  DEBUG               - Enable debug output (0/1, default: $(DEBUG))"
	@echo "  TLSRPGM_TTY         - Programmer serial port (default: $(TLSRPGM_TTY))"
	@echo ""
	@echo "Output Files:"
	@echo "  ELF: $(ELF_FILE)"
	@echo "  BIN: $(BIN_FILE)"
	@echo "  LST: $(LST_FILE)"

.PHONY: all build clean ota flash restart wipe flasher help
