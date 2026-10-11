"""Native C failure-injection for the exact Router NVM budget implementation."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
C = r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
static uint8_t flash_record[4], flash_valid, deny_read, deny_write;
static unsigned writes;
hal_nvm_status_t hal_nvm_read(uint8_t id, uint16_t len, uint8_t *dest) {
    assert(id == NV_ITEM_ROUTER_RESCUE_BUDGET && len == 4);
    if (deny_read) return HAL_NVM_ERROR;
    if (!flash_valid) return HAL_NVM_NOT_FOUND;
    memcpy(dest, flash_record, 4);
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id, uint16_t len, uint8_t *data) {
    assert(id == NV_ITEM_ROUTER_RESCUE_BUDGET && len == 4);
    writes++;
    if (deny_write) return HAL_NVM_ERROR;
    memcpy(flash_record, data, 4);
    flash_valid = 1;
    return HAL_NVM_SUCCESS;
}
#include "telink/hal/router_recovery_budget.c"
static void reboot_simulated(void) {
    budget_loaded = false;
    used_reboots = 0;
    good_run_started = false;
    good_replies = 0;
}
int main(void) {
    assert(bseed_router_rescue_budget_consume());
    assert(writes == 1);
    reboot_simulated();
    assert(bseed_router_rescue_budget_consume());
    reboot_simulated();
    assert(!bseed_router_rescue_budget_consume()); /* survives reboot */
    assert(writes == 2);

    /* Traffic that fails after healthy replies must reset the stable window. */
    uint32_t t = 1000u;
    bseed_router_rescue_budget_note_good_coordinator(t);
    for (int i=1;i<24;i++) bseed_router_rescue_budget_note_good_coordinator(t+6000u*i);
    bseed_router_rescue_budget_note_failure();
    bseed_router_rescue_budget_note_good_coordinator(t+500000u);
    for (int i=1;i<24;i++)
        bseed_router_rescue_budget_note_good_coordinator(t+500000u+i*60000u);
    assert(writes==2 && !bseed_router_rescue_budget_consume());
    bseed_router_rescue_budget_note_good_coordinator(t+500000u+12u*60u*60u*1000u);
    assert(writes==3);
    reboot_simulated();
    assert(bseed_router_rescue_budget_consume()); /* prolonged proof restores budget */

    reboot_simulated();
    flash_record[3] ^= 1u; /* invalid CRC must fail closed */
    assert(!bseed_router_rescue_budget_consume());
    flash_valid=0; reboot_simulated();
    deny_read=1; assert(!bseed_router_rescue_budget_consume());
    deny_read=0; flash_valid=0; reboot_simulated(); deny_write=1;
    assert(!bseed_router_rescue_budget_consume());
    return 0;
}
"""

def test_router_nv_budget_survives_restarts_and_fails_closed(tmp_path):
    source = tmp_path / "budget.c"
    binary = tmp_path / "budget"
    source.write_text(C, encoding="utf-8")
    build = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=20
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
