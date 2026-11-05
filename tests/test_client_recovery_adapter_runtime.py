"""Exercise the production mains Client probe/watchdog and NVM modules on CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_client_probe_and_backoff_with_persistent_budget(tmp_path):
    source = (ROOT / "src/telink/hal/zigbee_network.c").read_text(encoding="utf8")
    section = source.split("#ifdef BSEED_MAINS_CLIENT\n\n/* A parent poll ACK", 1)[1]
    section = "/* A parent poll ACK" + section.split(
        "\n#endif\n\n// Telink BDB storage", 1)[0]
    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "hal/bseed_recovery_core.h"
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
#define ZDO_SUCCESS 0
#define TELINK_NETWORK_RECOVERY_IDLE 0
#define TELINK_NETWORK_RECOVERY_REJOIN 1
typedef struct { uint16_t nwk_addr_interest; } zdo_node_descriptor_req_t;
typedef struct __attribute__((packed)) {
    uint8_t seq_num, status; uint16_t nwk_addr_interest;
} zdo_node_descript_resp_t;
typedef struct { uint16_t src_addr, length; uint8_t *zpdu; } zdo_zdpDataInd_t;
static struct { uint8_t extAddress[8]; } g_zbMacPib;
static uint32_t now, network_last_start_attempt;
static int network_recovery_state;
static bool joined=true, factory_new, ota_fast_poll_active, reject_probe, synchronous;
static uint8_t next_sequence;
static unsigned probes, reboots;
static void (*probe_callback)(void *);
static uint32_t hal_millis(void) { return now; }
static bool zb_isDeviceJoinedNwk(void) { return joined; }
static bool zb_isDeviceFactoryNew(void) { return factory_new; }
static void schedule_reboot(uint16_t delay) { assert(delay==2000); reboots++; }
static uint8_t zb_zdoNodeDescReq(uint16_t dst, zdo_node_descriptor_req_t *req,
                                 uint8_t *seq, void (*callback)(void *)) {
    assert(dst==0 && req->nwk_addr_interest==0); probes++;
    if (reject_probe) return 1;
    *seq=++next_sequence; probe_callback=callback;
    if (synchronous) {
        zdo_zdpDataInd_t ind={0}; zdo_node_descript_resp_t rsp={*seq,0,0};
        ind.length=sizeof(rsp); ind.zpdu=(uint8_t *)&rsp; callback(&ind);
    }
    return 0;
}
static uint8_t records[2][4]; static bool present[2], fail70;
hal_nvm_status_t hal_nvm_read(uint8_t id,uint16_t size,uint8_t *out) {
    assert(size==4 && (id==70 || id==71));
    if(id==70 && fail70) return HAL_NVM_ERROR;
    if(!present[id-70]) return HAL_NVM_NOT_FOUND;
    memcpy(out,records[id-70],4); return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id,uint16_t size,uint8_t *in) {
    assert(size==4 && (id==70 || id==71));
    if(id==70 && fail70) return HAL_NVM_ERROR;
    memcpy(records[id-70],in,4); present[id-70]=true; return HAL_NVM_SUCCESS;
}
#include "telink/hal/router_recovery_budget.c"
#include "telink/hal/recovery_wedge_credit.c"
'''
    cases = r'''
int main(void) {
    telink_zigbee_hal_client_recovery_tick();
    now=631000u; reject_probe=true; telink_zigbee_hal_client_recovery_tick();
    assert(probes==1 && !client_coord_core.probe_pending && !client_coord_core.probe_failures);
    now+=631000u; reject_probe=false; synchronous=true;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==2 && !client_coord_core.probe_pending && client_outage_since_ms==now);
    synchronous=false; now+=631000u; telink_zigbee_hal_client_recovery_tick();
    assert(client_coord_core.probe_pending);
    zdo_zdpDataInd_t ind={0};
    zdo_node_descript_resp_t rsp={next_sequence,0,0};
    ind.length=sizeof(rsp); ind.src_addr=0x1234; ind.zpdu=(uint8_t *)&rsp;
    probe_callback(&ind); assert(client_coord_core.probe_pending);
    now+=31000u; telink_zigbee_hal_client_recovery_tick();
    assert(client_coord_core.probe_failures==1);
    now+=60000u; telink_zigbee_hal_client_recovery_tick();
    assert(client_coord_core.probe_pending);
    now+=31000u; telink_zigbee_hal_client_recovery_tick();
    assert(bseed_recovery_degraded(&client_coord_core));
    assert(budget_store(2) && credit_store(4));
    budget_loaded=budget_valid=false; credit_loaded=credit_usable=wedge_active=false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_REJOIN;
    network_last_start_attempt=now; client_outage_since_ms=now; joined=false;
    ota_fast_poll_active=true; now+=61u*60u*1000u;
    telink_zigbee_hal_client_recovery_tick(); assert(reboots==0);
    ota_fast_poll_active=false; fail70=true; telink_zigbee_hal_client_recovery_tick();
    assert(reboots==0 && records[1][1]==4);
    /* A storage error latches closed until reboot; then confirmed exhaustion
     * can consume the independently verified slow credit. */
    fail70=false; budget_loaded=budget_valid=false;
    telink_zigbee_hal_client_recovery_tick();
    assert(reboots==1 && client_reboot_scheduled && records[1][1]==0);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_REJOIN);
    return 0;
}
'''
    cfile = tmp_path / "client_adapter.c"
    binary = tmp_path / "client_adapter"
    cfile.write_text(prelude + section + cases, encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "src"), str(cfile), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
