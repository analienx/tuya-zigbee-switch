"""Run Round-7 Router IDLE-quarantine rescue against real item-70/item-71 C NVM.

GitHub-hosted native test: the SDK callback lacks an operation token, so
no competing rejoin may start; verified outage + persisted wedged uptime must
eventually earn exactly one fail-closed checkpoint even after budget exhaustion.
"""
from pathlib import Path
import subprocess

from test_telink_router_recovery_state_runtime import NETWORK, PRELUDE

ROOT = Path(__file__).resolve().parents[1]


def test_quarantined_router_credit_and_fail_closed_restart(tmp_path):
    prelude = PRELUDE.split("static bool bseed_router_rescue_budget_consume", 1)[0]
    prelude = prelude.replace(
        "typedef enum { BSEED_BUDGET_CONSUMED, BSEED_BUDGET_EXHAUSTED,\n"
        "               BSEED_BUDGET_STORAGE_ERROR } bseed_budget_result_t;\n", ""
    )
    source = NETWORK.read_text(encoding="utf-8")
    router = "/* A Router" + source.split(
        "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n\n/* A Router", 1
    )[1].split("\n#endif\n\n// Telink ZDO callbacks", 1)[0]
    nvm = r"""
#include <string.h>
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
static uint8_t flash[2][4];
static bool present[2], fail_read70, fail_write70;
hal_nvm_status_t hal_nvm_read(uint8_t id, uint16_t size, uint8_t *out) {
    assert(size==4 && (id==70 || id==71));
    if(id==70 && fail_read70) return HAL_NVM_ERROR;
    if(!present[id-70]) return HAL_NVM_NOT_FOUND;
    memcpy(out,flash[id-70],4);
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id, uint16_t size, uint8_t *data) {
    assert(size==4 && (id==70 || id==71));
    if(id==70 && fail_write70) return HAL_NVM_ERROR;
    memcpy(flash[id-70],data,4);
    present[id-70]=true;
    return HAL_NVM_SUCCESS;
}
#include "telink/hal/router_recovery_budget.c"
#include "telink/hal/recovery_wedge_credit.c"
"""
    cases = r"""
static void boot(void) {
    budget_loaded=budget_valid=false; used_reboots=0u;
    good_run_started=false; good_replies=0u;
    credit_loaded=credit_usable=wedge_active=false; credit_chunks=0u;
    router_reboot_scheduled=false;
    router_one_shot_active=router_one_shot_stalled=false;
    router_one_shot_attempted=false; router_one_shot_attempts=0u;
    router_probe_initialized=router_probe_pending=false;
    router_probe_failures=router_probe_rejections=0u;
    router_positive_seen=router_inbound_seen=false;
    router_observation_started=router_not_joined_observed=false;
    router_last_positive_ms=router_last_inbound_ms=0u;
    router_last_probe_ms=router_recovery_since_ms=0u;
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    network_sdk_backoff_started_once=false;
    network_sdk_steering_started_once=false;
    factory_new=ota_active=false; joined=true;
    now=1000000u; rescue_calls=probes=rejoin_calls=0u;
    fail_read70=fail_write70=false;
}
static void records(uint8_t used, uint8_t credit) {
    memset(flash,0,sizeof(flash));
    present[0]=present[1]=false;
    boot();
    assert(budget_store(used) && credit_store(credit));
    boot(); /* CPU state lost, NVM persistent */
}
static void joined_quarantine(void) {
    joined=true;
    network_sdk_backoff_started_once=true; /* accepted A terminated */
    router_observation_started=true;
    router_observation_epoch_ms=now;
    router_probe_initialized=true;
    router_last_probe_ms=now;
    router_probe_failures=ROUTER_PROBE_FAILURE_THRESHOLD;
}
static void unjoined_quarantine(void) {
    joined=false;
    router_one_shot_attempted=true; /* accepted A terminal; no new owner */
}
static void qualify_joined(void) {
    telink_zigbee_hal_router_recovery_tick();
    assert(!router_reboot_scheduled && rejoin_calls==0);
    now+=ROUTER_ISOLATED_REBOOT_MS+15u*60u*1000u+1u;
    telink_zigbee_hal_router_recovery_tick();
    assert(!router_reboot_scheduled && wedge_active);
    assert(rejoin_calls==0);
}
static void qualify_unjoined(void) {
    telink_zigbee_hal_router_recovery_tick();
    assert(router_not_joined_observed && !wedge_active);
    now+=ROUTER_RECOVERY_STALL_MS+15u*60u*1000u+1u;
    telink_zigbee_hal_router_recovery_tick();
    assert(!router_reboot_scheduled && wedge_active);
    assert(rejoin_calls==0);
}
static void advance_chunks(unsigned n) {
    for(unsigned i=0u;i<n;i++) {
        now+=WEDGE_CREDIT_CHUNK_MS;
        telink_zigbee_hal_router_recovery_tick();
        assert(rejoin_calls==0u);
    }
}
int main(void) {
    /* R7a: accepted backoff A ended; IDLE/JOINED, no coordinator reply.
     * Exhausted normal budget must not strand an uncorrelated SDK owner.
     * Four REAL six-hour NVM checkpoints cause exactly one slow-lane reboot. */
    records(2u,0u);
    joined_quarantine();
    qualify_joined();
    advance_chunks(3u);
    assert(credit_chunks==3u && flash[1][1]==3u && rescue_calls==0u);
    advance_chunks(1u);
    assert(rescue_calls==1u && router_reboot_scheduled);
    assert(flash[0][1]==2u && flash[1][1]==0u);
    now+=WEDGE_CREDIT_CHUNK_MS;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1u);

    /* R7b: terminal one-shot A, later not-joined but no safe new owner;
     * the monotonic unjoined epoch survives an unverified JOINED flap. */
    records(2u,0u);
    unjoined_quarantine();
    telink_zigbee_hal_router_recovery_tick();
    now+=15000u; joined=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(router_not_joined_observed);
    joined=false;
    now+=ROUTER_RECOVERY_STALL_MS+15u*60u*1000u+1u;
    telink_zigbee_hal_router_recovery_tick();
    assert(wedge_active && !router_reboot_scheduled);
    advance_chunks(4u);
    assert(rescue_calls==1u && router_reboot_scheduled);
    assert(flash[0][1]==2u && flash[1][1]==0u);

    /* R7c: valid full slow credit must never override corrupted/IO budget. */
    records(2u,4u);
    flash[0][3]^=1u;
    joined_quarantine();
    qualify_joined();
    assert(rescue_calls==0u && flash[1][1]==4u);
    records(2u,4u);
    joined_quarantine(); fail_read70=true;
    qualify_joined();
    assert(rescue_calls==0u && flash[1][1]==4u);
    records(1u,4u);
    joined_quarantine(); fail_write70=true;
    qualify_joined();
    assert(rescue_calls==0u && flash[1][1]==4u);

    /* A verified healthy coordinator reply resets the isolation evidence
     * without fabricating a fresh SDK owner or losing stored partial chunks. */
    records(2u,0u); joined_quarantine(); qualify_joined();
    advance_chunks(1u);
    assert(flash[1][1]==1u);
    router_positive_seen=true; router_last_positive_ms=now;
    router_probe_failures=router_probe_rejections=0u;
    now+=60000u;
    telink_zigbee_hal_router_recovery_tick();
    assert(!wedge_active && !router_reboot_scheduled);
    assert(flash[1][1]==1u);

    /* True cold restart persists item71 only at complete 6h boundaries.
     * A separate later qualifying wedge can accrue the remaining chunks. */
    boot(); joined_quarantine(); qualify_joined();
    advance_chunks(3u);
    assert(rescue_calls==1u && flash[1][1]==0u);

    /* Signed-clock wrap is not used to measure the unjoined outage. */
    records(2u,4u);
    now=UINT32_MAX-1200000u; unjoined_quarantine();
    telink_zigbee_hal_router_recovery_tick();
    now+=ROUTER_RECOVERY_STALL_MS+15u*60u*1000u+1u;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1u && router_reboot_scheduled);
    return 0;
}
"""
    cfile=tmp_path/"router_quarantine_nvm.c"
    binary=tmp_path/"router_quarantine_nvm"
    cfile.write_text(prelude+nvm+router+cases,encoding="utf-8")
    built=subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror",
                          "-Wno-unused-variable","-I",str(ROOT/"src"),
                          str(cfile),"-o",str(binary)],
                         capture_output=True,text=True,timeout=30)
    assert built.returncode==0,built.stdout+built.stderr
    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stdout+run.stderr
