"""Execute the actual Telink Router recovery state machine with SDK fault injection.

Runs only in GitHub-hosted Actions, like all tests in this public repository.
Unlike static source guards, this extracts the production C function and
executes it against mocked, deterministic Telink callbacks/timer conditions.
"""
from pathlib import Path
import subprocess
import re

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"

PRELUDE = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <assert.h>
#include <limits.h>
#include "hal/router_recovery_policy.h"
#include "hal/bseed_recovery_core.h"
#include "telink/hal/recovery_diagnostics.h"
bool bseed_recovery_diag_record(bseed_rescue_reason_t reason, uint8_t sdk_state) {
    (void)reason; (void)sdk_state; return true;
}
typedef enum { BSEED_BUDGET_CONSUMED, BSEED_BUDGET_EXHAUSTED,
               BSEED_BUDGET_STORAGE_ERROR } bseed_budget_result_t;
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
    uint8_t *zpdu;
} zdo_zdpDataInd_t;

typedef struct {uint32_t rejoin_start, rejoin_failure, rejoin_success, parent_loss;} health_t;
static health_t firmware_health;
static void firmware_health_increment(uint32_t *counter) {(*counter)++;}
#define TELINK_NETWORK_RECOVERY_IDLE 0
#define TELINK_NETWORK_RECOVERY_REJOIN 1
static int network_recovery_state;
static uint32_t network_last_start_attempt;
static bool network_sdk_backoff_started_once;
static bool network_sdk_steering_started_once;
static struct { uint8_t extAddress[8]; } g_zbMacPib;
static struct {uint8_t scanDuration;} g_bdbAttrs;
static uint32_t now, probes, rejoin_calls, rescue_calls, budget_used, positives, failed;
static bool joined = true, factory_new, ota_active;
static bool slow_credit_ready;
static bool budget_storage_error;
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
static bseed_budget_result_t bseed_router_rescue_budget_try_consume(void) {
    if (budget_storage_error) return BSEED_BUDGET_STORAGE_ERROR;
    return bseed_router_rescue_budget_consume() ? BSEED_BUDGET_CONSUMED : BSEED_BUDGET_EXHAUSTED;
}
static bool bseed_wedge_credit_tick(uint32_t now_ms, bool wedged)
    {(void)now_ms; return wedged && slow_credit_ready;}
static bool bseed_wedge_credit_consume(void)
    {if(!slow_credit_ready)return false;slow_credit_ready=false;return true;}
static void bseed_router_rescue_budget_note_good_coordinator(uint32_t timestamp)
    {(void)timestamp;positives++;}
static void bseed_router_rescue_budget_note_failure(void) {failed++;}
"""
TESTS = r"""
int main(void) {
    /* First healthy Router period must not scan, rejoin or reboot. */
    telink_zigbee_hal_router_recovery_tick();
    assert(probes==0 && rejoin_calls==0 && rescue_calls==0);
    assert(!router_positive_seen && !router_inbound_seen);
    assert(router_last_positive_ms==0 && router_last_inbound_ms==0);
    now=600000; telink_zigbee_hal_router_recovery_tick();
    assert(probes==1 && router_probe_pending);
    /* Wrong source NWK / stale sequence cannot reset this timeout. */
    zdo_zdpDataInd_t ind={0};
    uint8_t payload[8]={0}; ind.zpdu=payload;
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
    network_recovery_state=1; now+=1;
    telink_zigbee_hal_router_recovery_tick();
    assert(router_probe_failures==1 && !router_positive_seen);
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    telink_zigbee_hal_router_recovery_tick();
    assert(router_probe_failures==1 && !router_positive_seen);
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

    /* Even a validated coordinator reply cannot prove an orphaned SDK
     * operation ended; do not permit another scan on its evidence alone. */
    joined=true;
    /* A reboot has already been scheduled above; keep it pending. */
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
    assert(router_one_shot_stalled && rejoin_calls==1);
    router_reboot_scheduled=false;
    now+=3600000; telink_zigbee_hal_router_recovery_tick();
    assert(router_one_shot_stalled && rejoin_calls==1 && rescue_calls==1);
    /* A later outage with the ordinary budget exhausted can still consume
     * accumulated wedge credit and reset the orphaned SDK state. */
    joined=false; budget_used=2; router_reboot_scheduled=false;
    slow_credit_ready=true;
    budget_storage_error=true;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==1 && !router_reboot_scheduled && slow_credit_ready);
    budget_storage_error=false;
    telink_zigbee_hal_router_recovery_tick();
    assert(rescue_calls==2 && router_reboot_scheduled && !slow_credit_ready);
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


def test_joined_sdk_backoff_without_coordinator_proof_cannot_starve(tmp_path):
    """An SDK backoff that reports JOINED cannot mask a multi-hour outage."""
    source = NETWORK.read_text(encoding="utf-8")
    section = "/* A Router" + source.split(
        "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n\n/* A Router", 1
    )[1].split("\n#endif\n\n// Telink ZDO callbacks", 1)[0]
    cases = r"""
int main(void) {
    now=1000000u; joined=true;
    network_recovery_state=TELINK_NETWORK_RECOVERY_REJOIN;
    network_last_start_attempt=now;
    telink_zigbee_hal_router_recovery_tick();
    assert(probes==0 && rescue_calls==0 && !router_positive_seen);

    /* The SDK is still marked JOINED. Its owner gate prevents normal
     * coordinator probes, but must not prevent watchdog escalation. */
    now+=ROUTER_BACKOFF_JOINED_QUIET_MS+15u*60u*1000u;
    telink_zigbee_hal_router_recovery_tick();
    assert(probes==0 && rejoin_calls==0);
    assert(rescue_calls==1 && budget_used==1);

    /* A separate boot with a genuinely active neighbor receives the longer
     * grace, preventing an unnecessary scan while it serves children. */
    return 0;
}
"""
    harness = tmp_path / "false_joined.c"
    binary = tmp_path / "false_joined"
    harness.write_text(PRELUDE + "\n" + section + "\n" + cases, encoding="utf-8")
    built = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                           "-I", str(ROOT / "src"), str(harness), "-o", str(binary)],
                          capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    outcome = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


def test_hash_spreads_sequential_router_addresses(tmp_path):
    """Regression: three raw IEEE bytes concentrated 80 Routers into 80ms."""
    source = tmp_path / "jitter.c"
    binary = tmp_path / "jitter"
    source.write_text(r"""
#include <assert.h>
#include <stdint.h>
#include "hal/bseed_recovery_core.h"
int main(void) {
    uint8_t ieee[8]={0xa4,0xc1,0x38,0xcd,0xf9,0x63,0xea,0};
    uint32_t min=900000u,max=0u;
    unsigned slots[90]={0};
    for(unsigned i=0;i<80u;i++) {
        ieee[7]=(uint8_t)i;
        uint32_t v=bseed_recovery_action_stagger_ms(ieee,0x52524a4eu,900000u);
        if(v<min)min=v;
        if(v>max)max=v;
        slots[v/10000u]++;
        assert(bseed_recovery_action_stagger_ms(ieee,0x52524254u,900000u)!=v);
    }
    assert(max-min>600000u);
    unsigned used=0,peak=0;
    for(unsigned i=0;i<90u;i++) {
        if(slots[i])used++;
        if(slots[i]>peak)peak=slots[i];
    }
    assert(used>40u && peak<6u);
    return 0;
}
""",encoding="utf-8")
    built = subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror",
                            "-I",str(ROOT/"src"),str(source),"-o",str(binary)],
                           capture_output=True,text=True,timeout=30)
    assert built.returncode==0, built.stdout+built.stderr
    run = subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
    assert run.returncode==0, run.stdout+run.stderr

def test_late_bdb_callback_cannot_reassign_router_radio_owner(tmp_path):
    """Production BDB callback + both SDK recovery entry points.

    A backoff terminal callback can repeat after JOINED. Because Telink BDB
    supplies no operation token, we quarantine subsequent owners until reboot
    instead of attributing an old SUCCESS to a newer one-shot.
    """
    source = NETWORK.read_text(encoding="utf-8")
    router = "/* A Router" + source.split(
        "#if ZB_ROUTER_ROLE && !ZB_COORDINATOR_ROLE\n\n/* A Router", 1
    )[1].split("\n#endif\n\n// Telink ZDO callbacks", 1)[0]
    rejoin = "static bool start_rejoin_with_backoff(void) {" + source.split(
        "static bool start_rejoin_with_backoff(void) {", 1
    )[1].split("static void notify_about_network_status_change()", 1)[0]
    cb = "void bdb_commissioning_callback(u8 status, void *arg) {" + source.split(
        "void bdb_commissioning_callback(u8 status, void *arg) {", 1
    )[1].split("void bdb_identify_callback", 1)[0]
    statuses = sorted(set(re.findall(r"BDB_COMMISSION_STA_\w+", cb)))
    status_defines = "\n".join(
        f"#define {name} {i + 1}" for i, name in enumerate(statuses)
    )
    extras = r"""
#define BSEED_PM_B28WRPVX 1
static unsigned backoff_calls, announces;
static bool network_start_retry_pending;
static bool network_start_ready(void) {
    return !network_start_retry_pending ||
           (uint32_t)(now-network_last_start_attempt)>=5000u;
}
static void network_start_attempt(void) {
    network_start_retry_pending=true; network_last_start_attempt=now;
}
static bool request_rejoin_with_backoff(bool parent_stale);
static u8 zb_rejoinReqWithBackOff(uint32_t mask,u8 duration) {
    (void)mask; (void)duration; backoff_calls++; return RET_OK;
}
static void app_rearm_network_announcement(void) { announces++; }
static void telink_zigbee_hal_request_ota_query(void) {}
static void notify_about_network_status_change(void) {}
"""
    scenario = r"""
int main(void) {
    /* A: SDK-managed backoff is the one accepted recovery owner. */
    now=20000u; joined=false;
    assert(request_rejoin_with_backoff(false));
    assert(backoff_calls==1 && network_sdk_backoff_started_once);
    joined=true;
    bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,0);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);

    /* Simulate real coordinator-response timeouts after A: the Router
     * would otherwise qualify a one-shot B during this same boot. */
    now=2u*60u*60u*1000u;
    router_observation_started=true; router_observation_epoch_ms=0u;
    router_probe_initialized=true; router_last_probe_ms=now;
    router_probe_failures=ROUTER_PROBE_FAILURE_THRESHOLD;
    telink_zigbee_hal_router_recovery_tick();
    assert(rejoin_calls==0 && !router_one_shot_active);
    /* Old A SUCCESS/FALSE completion is untagged and may duplicate:
     * B was never started, so it cannot lose ownership to old A. */
    bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,0);
    bdb_commissioning_callback(BDB_COMMISSION_STA_REJOIN_FAILURE,0);
    assert(!router_one_shot_active && backoff_calls==1);
    joined=false;
    assert(!request_rejoin_with_backoff(false) && backoff_calls==1);

    /* A new reboot epoch may start one Router one-shot B. */
    network_sdk_backoff_started_once=false; /* modeled software reboot */
    network_start_retry_pending=false;
    joined=true; router_probe_initialized=true;
    now+=ROUTER_REJOIN_COOLDOWN_MS+20u*60u*1000u;
    router_last_probe_ms=now; router_probe_failures=4u;
    telink_zigbee_hal_router_recovery_tick();
    assert(rejoin_calls==1 && router_one_shot_active);
    bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,0);
    assert(!router_one_shot_active && router_one_shot_attempted);
    joined=false;
    /* A delayed B duplicate cannot clear a later backoff: it is refused. */
    assert(!request_rejoin_with_backoff(false) && backoff_calls==1);
    bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,0);
    assert(backoff_calls==1 && !router_one_shot_active);
    return 0;
}
"""
    path = tmp_path / "router_bdb_ownership.c"
    binary = tmp_path / "router_bdb_ownership"
    path.write_text(PRELUDE + "\n" + status_defines + "\n" + extras +
                    "\n" + router + "\n" + rejoin + "\n" + cb +
                    "\n" + scenario, encoding="utf-8")
    result = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-I", str(ROOT / "src"), str(path),
         "-o", str(binary)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
