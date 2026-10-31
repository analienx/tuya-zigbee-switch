"""Execute the actual Telink Router recovery state machine with SDK fault injection.

Runs only in GitHub-hosted Actions, like all tests in this public repository.
Unlike static source guards, this extracts the production C function and
executes it against mocked, deterministic Telink callbacks/timer conditions.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"

PRELUDE = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include <limits.h>
#include "hal/router_recovery_policy.h"
#define ZB_ROUTER_ROLE 1
#define ZB_COORDINATOR_ROLE 0
#define RET_OK 0
#define ZDO_SUCCESS 0
#define REJOIN_SECURITY 1
typedef uint8_t u8;
typedef uint8_t zdo_status_t;
typedef struct {uint16_t nwk_addr_interest;} zdo_node_descriptor_req_t;
typedef struct __attribute__((packed)) {
    uint8_t seq_num;
    uint8_t status;
    uint16_t nwk_addr_interest;
} zdo_node_descript_resp_t;
typedef struct {
    uint16_t src_addr;
    uint16_t length;
    uint8_t zpdu[8];
} zdo_zdpDataInd_t;

typedef struct {uint32_t rejoin_start, rejoin_failure;} health_t;
static health_t firmware_health;
static void firmware_health_increment(uint32_t *counter) {(*counter)++;}
#define TELINK_NETWORK_RECOVERY_IDLE 0
static int network_recovery_state;
static struct { uint8_t extAddress[8]; } g_zbMacPib;
static struct {uint8_t scanDuration;} g_bdbAttrs;
static uint32_t now, probes, rejoin_calls, rescue_calls, budget_used, positives, failed;
static bool joined = true, factory_new, ota_active;
static uint8_t pending_sequence;
static void (*zdo_callback)(void *);
static uint32_t hal_millis(void) {return now;}
static bool zb_isDeviceJoinedNwk(void) {return joined;}
static bool zb_isDeviceFactoryNew(void) {return factory_new;}
static bool telink_zigbee_hal_router_ota_in_progress(void) {return ota_active;}
static uint32_t zb_apsChannelMaskGet(void) {return 0;}
static void zb_rejoinSecModeSet(uint8_t mode) {assert(mode==REJOIN_SECURITY);}
static uint8_t zb_rejoinReq(uint32_t m,uint8_t d)
    {(void)m;(void)d;rejoin_calls++;return RET_OK;}
static zdo_status_t zb_zdoNodeDescReq(uint16_t dst,
                                    zdo_node_descriptor_req_t *req,
                                    uint8_t *seq, void (*cb)(void *)) {
    assert(dst==0 && req->nwk_addr_interest==0);
    probes++; *seq=(uint8_t)probes; pending_sequence=*seq; zdo_callback=cb;
    return ZDO_SUCCESS;
}
static void schedule_reboot(uint32_t ms) {assert(ms==2000);rescue_calls++;}
static bool bseed_router_rescue_budget_consume(void)
    {if(budget_used>=2)return false;budget_used++;return true;}
static void bseed_router_rescue_budget_note_good_coordinator(uint32_t timestamp)
    {(void)timestamp;positives++;}
static void bseed_router_rescue_budget_note_failure(void) {failed++;}
"""
TESTS = r"""
int main(void) {
    /* First healthy Router period must not scan, rejoin or reboot. */
    telink_zigbee_hal_router_recovery_tick();
    assert(probes==0 && rejoin_calls==0 && rescue_calls==0);
    now=600000; telink_zigbee_hal_router_recovery_tick();
    assert(probes==1 && router_probe_pending);
    /* Wrong source NWK / stale sequence cannot reset this timeout. */
    zdo_zdpDataInd_t ind={0};
    zdo_node_descript_resp_t reply={pending_sequence, ZDO_SUCCESS, 0};
    ind.src_addr=0x1234; ind.length=sizeof(reply);
    __builtin_memcpy(ind.zpdu,&reply,sizeof(reply));
    zdo_callback(&ind);
    assert(router_probe_pending);
    ind.src_addr=0;
    reply.seq_num++;
    __builtin_memcpy(ind.zpdu,&reply,sizeof(reply));
    zdo_callback(&ind);
    assert(router_probe_pending);
    /* Timed-out ZDO requests count but must not cause an immediate rejoin. */
    now+=31000; telink_zigbee_hal_router_recovery_tick();
    assert(router_probe_failures==1 && rejoin_calls==0);
    for(unsigned i=0;i<2;i++) {
        now+=600000; telink_zigbee_hal_router_recovery_tick();
        assert(router_probe_pending);
        now+=31000; telink_zigbee_hal_router_recovery_tick();
        assert(router_probe_failures==i+2);
    }
    assert(rejoin_calls==0);
    /* Fourth confirmed unanswered coordinator request crosses the recovery
     * threshold; its same tick can legitimately launch the one-shot. */
    now+=600000; telink_zigbee_hal_router_recovery_tick();
    assert(router_probe_pending);
    now+=31000; telink_zigbee_hal_router_recovery_tick();
    assert(rejoin_calls==1 && router_one_shot_active);
    assert(!router_probe_pending && rescue_calls==0);

    /* A one-shot temporarily makes the Router NOT_JOINED.
     * It must not start another one-shot or schedule rescue prematurely. */
    joined=false;
    now+=20000; telink_zigbee_hal_router_recovery_tick();
    assert(router_one_shot_active && rejoin_calls==1 && rescue_calls==0);
    ota_active=true; now+=3600000;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==0 && budget_used==0);
    ota_active=false;
    /* Stuck SDK callback gets an orphan marker, then at most one rescue. */
    telink_zigbee_hal_router_recovery_tick();
    assert(router_one_shot_stalled && rejoin_calls==1);
    now+=3600000; telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && budget_used==1);
    now+=3600000; telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && budget_used==1);

    /* The orphan marker survives rejoin until the coordinator path is
     * proven again. A validated reply must release it, otherwise no later
     * one-shot rejoin could ever start in this uptime. */
    joined=true;
    router_reboot_scheduled=false;
    assert(router_one_shot_stalled);
    router_probe_initialized=false;
    router_probe_failures=0;
    now+=100; telink_zigbee_hal_router_recovery_tick();
    now+=600000; telink_zigbee_hal_router_recovery_tick();
    assert(router_probe_pending);
    ind.src_addr=0;
    reply.seq_num=pending_sequence;
    __builtin_memcpy(ind.zpdu,&reply,sizeof(reply));
    zdo_callback(&ind);
    assert(!router_probe_pending && router_probe_failures==0 && positives==1);
    assert(!router_one_shot_stalled && rejoin_calls==1);
    return 0;
}
"""

def test_telink_sdk_callback_route_recovery_and_rescue(tmp_path):
    source = NETWORK.read_text(encoding="utf-8")
    section = "/* A Router" + source.split(
        "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n\n/* A Router", 1
    )[1].split("\n#endif\n\n// Telink ZDO callbacks", 1)[0]
    harness = tmp_path / "telink_router_recovery.c"
    binary = tmp_path / "telink_router_recovery"
    harness.write_text(PRELUDE + "\n" + section + "\n" + TESTS, encoding="utf-8")
    built = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I",
         str(ROOT / "src"), str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=30
    )
    assert built.returncode == 0, built.stdout + built.stderr
    test = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert test.returncode == 0, test.stdout + test.stderr
