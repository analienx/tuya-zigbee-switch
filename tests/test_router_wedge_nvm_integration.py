"""Exercise the production Router fallback with both real NVM modules on CI."""
from pathlib import Path
import subprocess

from test_telink_router_recovery_state_runtime import NETWORK, PRELUDE

ROOT = Path(__file__).resolve().parents[1]


def test_router_slow_lane_never_bypasses_budget_io_errors(tmp_path):
    prelude = PRELUDE.split("static bool bseed_router_rescue_budget_consume", 1)[0]
    prelude = prelude.replace(
        "typedef enum { BSEED_BUDGET_CONSUMED, BSEED_BUDGET_EXHAUSTED,\n"
        "               BSEED_BUDGET_STORAGE_ERROR } bseed_budget_result_t;\n", ""
    )
    nvm = r'''
#include <string.h>
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
static uint8_t records[2][4];
static bool present[2], fail_read70, fail_write70, bad_readback70, written70;
hal_nvm_status_t hal_nvm_read(uint8_t id, uint16_t size, uint8_t *data) {
    assert(size==4 && (id==70 || id==71));
    unsigned i=id-70;
    if (id==70 && fail_read70) return HAL_NVM_ERROR;
    if (!present[i]) return HAL_NVM_NOT_FOUND;
    memcpy(data,records[i],4);
    if (id==70 && bad_readback70 && written70) data[3]^=1u;
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id, uint16_t size, uint8_t *data) {
    assert(size==4 && (id==70 || id==71));
    if (id==70 && fail_write70) return HAL_NVM_ERROR;
    memcpy(records[id-70],data,4); present[id-70]=true;
    if (id==70) written70=true;
    return HAL_NVM_SUCCESS;
}
#include "telink/hal/router_recovery_budget.c"
#include "telink/hal/recovery_wedge_credit.c"
'''
    source = NETWORK.read_text(encoding="utf-8")
    section = "/* A Router" + source.split(
        "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n\n/* A Router", 1
    )[1].split("\n#endif\n\n// Telink ZDO callbacks", 1)[0]
    cases = r'''
static void cold_boot(void) {
    budget_loaded=budget_valid=false; used_reboots=0;
    credit_loaded=credit_usable=wedge_active=false; credit_chunks=0;
    router_reboot_scheduled=false; router_one_shot_stalled=true;
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    network_last_start_attempt=0;
    router_one_shot_ms=0; router_observation_started=false;
    joined=false; factory_new=false; ota_active=false; now=1000000u;
    fail_read70=fail_write70=bad_readback70=written70=false;
}
static void full_credit(uint8_t used) {
    fail_read70=fail_write70=bad_readback70=false;
    assert(budget_store(used)); assert(credit_store(4));
    cold_boot();
}
int main(void) {
    full_credit(2); telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && router_reboot_scheduled && records[1][1]==0);
    full_credit(2); records[0][3]^=1u;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && records[1][1]==4);
    full_credit(2); fail_read70=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && records[1][1]==4);
    full_credit(1); fail_write70=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && records[1][1]==4);
    full_credit(1); bad_readback70=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && records[1][1]==4);
    full_credit(2); ota_active=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && records[1][1]==4);
    /* An SDK-owned backoff with no completion is a separate wedge from a
     * one-shot. Its persisted slow lane must work when item 70 is exhausted. */
    full_credit(2); router_one_shot_stalled=false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_REJOIN; now=0;
    telink_zigbee_hal_router_recovery_tick();
    now=46u*60u*1000u; telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==2 && router_reboot_scheduled && records[1][1]==0);
    return 0;
}
'''
    cfile = tmp_path / "router_wedge_nvm.c"
    binary = tmp_path / "router_wedge_nvm"
    cfile.write_text(prelude + nvm + section + cases, encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-I", str(ROOT / "src"),
                    str(cfile), "-o", str(binary)], check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
