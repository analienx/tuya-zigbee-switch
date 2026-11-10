"""Hosted native C tests for bounded recovery-reason NVM persistence."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
C = r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
static uint8_t stored[4];
static bool present, fail_write, fail_read;
static unsigned writes;
hal_nvm_status_t hal_nvm_read(uint8_t id, uint16_t size, uint8_t *out) {
    assert(id==NV_ITEM_RECOVERY_DIAGNOSTIC && size==4);
    if(fail_read) return HAL_NVM_ERROR;
    if(!present) return HAL_NVM_NOT_FOUND;
    memcpy(out,stored,4); return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id, uint16_t size, uint8_t *data) {
    assert(id==NV_ITEM_RECOVERY_DIAGNOSTIC && size==4);
    writes++;
    if(fail_write) return HAL_NVM_ERROR;
    memcpy(stored,data,4); present=true; return HAL_NVM_SUCCESS;
}
#include "telink/hal/recovery_diagnostics.c"
static void restart(void) {
    diag_loaded=diag_storage_ok=diag_present=false;
    diag_reason=diag_sdk_state=0;
}
int main(void) {
    uint8_t reason=123, state=123;
    assert(!bseed_recovery_diag_last(&reason,&state));
    assert(reason==0 && state==0);
    assert(bseed_recovery_diag_record(BSEED_RESCUE_REASON_ROUTER_BACKOFF,2u));
    assert(writes==1 && present);
    assert(bseed_recovery_diag_record(BSEED_RESCUE_REASON_ROUTER_BACKOFF,2u));
    assert(writes==1); /* no repeated flash writes on the same transition */
    restart();
    assert(bseed_recovery_diag_last(&reason,&state) && reason==2 && state==2);
    stored[1]^=1u;restart(); /* CRC catches reason mutation */
    assert(!bseed_recovery_diag_last(&reason,&state));
    assert(!bseed_recovery_diag_record(BSEED_RESCUE_REASON_ROUTER_ORPHAN,1u));
    assert(writes==1); /* corrupted prior record remains forensic evidence */
    present=false;restart();fail_write=true;
    assert(!bseed_recovery_diag_record(BSEED_RESCUE_REASON_CLIENT_BACKOFF,1u));
    assert(writes==2);
    fail_write=false;restart();fail_read=true;
    assert(!bseed_recovery_diag_record(BSEED_RESCUE_REASON_CLIENT_BACKOFF,1u));
    assert(writes==2);
    return 0;
}
"""


def test_recovery_diagnostic_record_is_bounded_and_fail_closed(tmp_path):
    source = tmp_path / "recovery_diagnostics.c"
    binary = tmp_path / "recovery_diagnostics"
    source.write_text(C, encoding="utf-8")
    build = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=30)
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
