#include "hal/system.h"
#pragma pack(push, 1)
#include "tl_common.h"
#include "zb_api.h"
#pragma pack(pop)
#include <stdbool.h>
#include <stdint.h>

void hal_system_reset(void) {
    // Telink 8258 system reset
    mcu_reset();
}

void hal_factory_reset(void) {
    zb_factoryReset();
}

#ifdef BSEED_MAINS_CLIENT
bool hal_role_change_reset(void) {
    const nv_module_t modules[] = {
        NV_MODULE_ZB_INFO, NV_MODULE_ADDRESS_TABLE, NV_MODULE_APS,
        NV_MODULE_ZCL,     NV_MODULE_OTA,           NV_MODULE_KEYPAIR,
    };
    bool ok = true;

    for (unsigned i = 0; i < sizeof(modules) / sizeof(modules[0]); i++) {
        if (nv_resetModule(modules[i]) != NV_SUCC) ok = false;
    }
    return ok;
}
#endif
