# Always-awake, non-routing Telink mains-client build.
#
# This deliberately does NOT modify the normal Makefile role matrix. Existing
# router and sleepy end_device builds therefore keep their exact compiler/link
# inputs. Use this makefile explicitly for experimental mains-powered leaf
# devices that must remain reachable while not participating in routing.

DEVICE_TYPE := client
TEL_CHIP := -DMCU_CORE_8258=1 -DEND_DEVICE=1 -DMCU_STARTUP_8258=1 -DBSEED_MAINS_CLIENT=1 -DZB_MAC_RX_ON_WHEN_IDLE=1
LIBS := -ldrivers_8258 -lzb_ed

include Makefile
