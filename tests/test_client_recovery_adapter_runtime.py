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
#include <stdio.h>
#include "hal/bseed_recovery_core.h"
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
#include "telink/hal/recovery_diagnostics.h"
bool bseed_recovery_diag_record(bseed_rescue_reason_t reason, uint8_t sdk_state) {
    (void)reason; (void)sdk_state; return true;
}
#define ZDO_SUCCESS 0
typedef uint8_t zdo_status_t;
#define TELINK_NETWORK_RECOVERY_IDLE 0
#define TELINK_NETWORK_RECOVERY_REJOIN 1
#define CLIENT_HEALTH_REJOIN_COOLDOWN_MS (15u * 60u * 1000u)
static bool client_health_recovery_attempted;
static uint32_t client_health_last_recovery_ms;
static bool network_start_ready(void) { return true; }
static bool request_rejoin_for_isolated_coordinator(void);
typedef struct { uint16_t nwk_addr_interest; } zdo_node_descriptor_req_t;
typedef struct __attribute__((packed)) {
    uint8_t seq_num, status; uint16_t nwk_addr_interest;
} zdo_node_descript_resp_t;
typedef struct { uint16_t src_addr, length; uint8_t *zpdu; } zdo_zdpDataInd_t;
static struct { uint8_t extAddress[8]; } g_zbMacPib;
static uint32_t now, network_last_start_attempt;
static int network_recovery_state;
static bool network_sdk_backoff_started_once;
static bool joined=true, factory_new, ota_fast_poll_active, reject_probe, synchronous;
static bool reject_rejoin;
static uint8_t next_sequence;
static unsigned probes, reboots, rejoin_starts;
static bool request_rejoin_for_isolated_coordinator(void) {
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
    if (network_sdk_backoff_started_once) return false;
    rejoin_starts++;
    if (reject_rejoin) return false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_REJOIN;
    network_sdk_backoff_started_once=true;
    network_last_start_attempt=now;
    return true;
}
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
    # A coordinator route may be broken despite healthy parent MAC ACKs.
    # Use production C, real coordinator probe bookkeeping and the same
    # operation-owner stub as the normal Client watchdog.
    isolation = r'''
int main(void) {
    telink_zigbee_hal_client_recovery_tick();
    now=631000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==1 && client_coord_core.probe_pending);
    now+=31000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(client_coord_core.probe_failures==1);
    now+=60000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==2 && client_coord_core.probe_pending);
    now+=31000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(bseed_recovery_degraded(&client_coord_core));
    assert(rejoin_starts==0);
    /* Both probes failed independently; no MAC-parent failure was needed.
     * The bounded qualification window must start exactly one SDK backoff. */
    now=CLIENT_COORD_ISOLATION_MS+CLIENT_COORD_REJOIN_JITTER_MS+100u;
    telink_zigbee_hal_client_recovery_tick();
    assert(rejoin_starts==1 && network_recovery_state==TELINK_NETWORK_RECOVERY_REJOIN);
    now+=60000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(rejoin_starts==1);
    return 0;
}
'''
    isolation_file = tmp_path / "client_isolation.c"
    isolation_binary = tmp_path / "client_isolation"
    isolation_file.write_text(prelude + section + isolation, encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "src"), str(isolation_file),
                    "-o", str(isolation_binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(isolation_binary)], check=True,
                   capture_output=True, text=True)

    # Persistent rejection is different from an accepted, unanswered ZDO:
    # it must never trigger on one BUSY return, but must qualify after
    # enough independent attempts and sufficient no-proof time.
    reject_always = r'''
int main(void) {
    telink_zigbee_hal_client_recovery_tick();
    reject_probe=true;
    for (unsigned i=0;i<8;i++) {
        now = 631000u + i * 660000u;
        telink_zigbee_hal_client_recovery_tick();
        assert(client_coord_core.probe_failures==0);
        if (i<6) assert(rejoin_starts==0 && reboots==0);
    }
    assert(client_probe_rejections>=CLIENT_COORD_REJECT_THRESHOLD);
    assert(client_rejections_started && rejoin_starts==1);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_REJOIN);
    unsigned prior=probes;
    joined=true; /* SDK may falsely report JOINED during its backoff. */
    now+=CLIENT_COORD_PROBE_MS+CLIENT_BACKOFF_WEDGE_MS;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==prior); /* no ZDO on top of SDK-owned scan */
    ota_fast_poll_active=true;
    now=network_last_start_attempt+CLIENT_BACKOFF_UNPROVEN_MS+
        CLIENT_COORD_REJOIN_JITTER_MS+100000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(reboots==0 && probes==prior);
    ota_fast_poll_active=false;
    telink_zigbee_hal_client_recovery_tick();
    assert(reboots==1 && probes==prior);
    return 0;
}
'''
    reject_file = tmp_path / "client_rejected_forever.c"
    reject_binary = tmp_path / "client_rejected_forever"
    reject_file.write_text(prelude + section + reject_always, encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "src"), str(reject_file),
                    "-o", str(reject_binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(reject_binary)], check=True, capture_output=True, text=True)

    # An accepted SDK rejoin cannot be started when the local queue remains
    # wedged; repeated synchronous rejection must still earn one checkpoint
    # rather than requiring an SDK owner that never existed.
    reject_rejoin = r'''
int main(void) {
    telink_zigbee_hal_client_recovery_tick();
    reject_probe=true; reject_rejoin=true;
    for(unsigned i=0;i<23u;i++) {
        now=631000u+i*660000u;
        telink_zigbee_hal_client_recovery_tick();
    }
    assert(rejoin_starts>0 && network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
    assert(client_coord_core.probe_failures==0);
    assert(reboots==1 && client_reboot_scheduled);
    assert(used_reboots==1u);
    return 0;
}
'''
    recovery_file = tmp_path / "client_queue_stall.c"
    recovery_binary = tmp_path / "client_queue_stall"
    recovery_file.write_text(prelude + section + reject_rejoin, encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "src"), str(recovery_file),
                    "-o", str(recovery_binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(recovery_binary)], check=True, capture_output=True, text=True)

    # Stale JOINED with SDK-owned backoff: not one probe may be transmitted.
    # Eventually the independent tenure watchdog must qualify a checkpoint.
    no_concurrent_probe = r'''
int main(void) {
    telink_zigbee_hal_client_recovery_tick();
    network_recovery_state=TELINK_NETWORK_RECOVERY_REJOIN;
    network_last_start_attempt=now;
    network_sdk_backoff_started_once=true;
    joined=true;
    now=CLIENT_COORD_PROBE_MS+10000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==0 && reboots==0);
    ota_fast_poll_active=true;
    now=CLIENT_BACKOFF_UNPROVEN_MS+CLIENT_COORD_REJOIN_JITTER_MS+90000u;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==0 && reboots==0);
    ota_fast_poll_active=false;
    telink_zigbee_hal_client_recovery_tick();
    assert(probes==0 && reboots==1 && client_reboot_scheduled);
    return 0;
}
'''
    owner_file = tmp_path / "client_sdk_owner.c"
    owner_binary = tmp_path / "client_sdk_owner"
    owner_file.write_text(prelude + section + no_concurrent_probe,
                          encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "src"), str(owner_file),
                    "-o", str(owner_binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(owner_binary)], check=True, capture_output=True, text=True)
