"""Exercise factory-new Telink steering BDB ownership on hosted native C.

The real BDB callback is uncorrelated. Terminal attempt A cannot be followed
by steering B in the same boot; a long, budgeted reboot restores an owner
without erasing commissioning identity or creating overlapping requests.
"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/telink/hal/zigbee_network.c"


def test_factory_steering_quarantine_and_safe_checkpoint(tmp_path):
    source = SOURCE.read_text(encoding="utf-8")
    state = "typedef enum {" + source.split("typedef enum {", 1)[1].split(
        "// Telink ZDO callbacks", 1
    )[0]
    rejoin = "static bool start_rejoin_with_backoff(void)" + source.split(
        "static bool start_rejoin_with_backoff(void)", 1
    )[1].split("void zdo_leave_indication_callback", 1)[0]
    callback = "void bdb_commissioning_callback(u8 status, void *arg) {" + source.split(
        "void bdb_commissioning_callback(u8 status, void *arg) {", 1
    )[1].split("void bdb_identify_callback", 1)[0]
    ensure = "void hal_zigbee_start_network_steering(void)" + source.split(
        "void hal_zigbee_start_network_steering(void)", 1
    )[1].split("hal_zigbee_status_t hal_zigbee_send_announce", 1)[0]
    constants = sorted(set(re.findall(r"BDB_COMMISSION_STA_\w+", callback)))
    defines = "\n".join(f"#define {name} {i + 1}" for i, name in enumerate(constants))
    prelude = r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include "hal/firmware_health.h"
#include "hal/bseed_recovery_core.h"
#include "telink/hal/recovery_diagnostics.h"
#define RET_OK 0
#define ZB_ROUTER_ROLE 0
#define ZB_COORDINATOR_ROLE 0
#define OTA_QUERY_INTERVAL 1u
typedef uint8_t u8;
typedef enum { HAL_ZIGBEE_NETWORK_NOT_JOINED,
               HAL_ZIGBEE_NETWORK_JOINED,
               HAL_ZIGBEE_NETWORK_JOINING } hal_zigbee_network_status_t;
typedef void (*hal_network_status_change_callback_t)(hal_zigbee_network_status_t);
static bool joined, factory_new=true, sync_terminal, reject_steer;
static uint8_t sync_terminal_status;
static uint32_t now, steer_calls, rejoin_calls, rescue_calls, budget_used;
static struct { uint8_t scanDuration; } g_bdbAttrs;
static struct { uint8_t extAddress[8]; } g_zbMacPib;
firmware_health_t firmware_health;
uint32_t hal_millis(void) { return now; }
static bool zb_isDeviceJoinedNwk(void) { return joined; }
static bool zb_isDeviceFactoryNew(void) { return factory_new; }
static uint32_t zb_apsChannelMaskGet(void) { return 0u; }
static u8 zb_rejoinReqWithBackOff(uint32_t channel,u8 duration) {
    (void)channel; (void)duration; rejoin_calls++; return RET_OK;
}
void bdb_commissioning_callback(u8 status, void *arg);
static u8 bdb_networkSteerStart(void) {
    steer_calls++;
    if (reject_steer) return 0x80u;
    if (sync_terminal) bdb_commissioning_callback(sync_terminal_status,0);
    return RET_OK;
}
static void ota_queryStart(uint16_t interval) { (void)interval; }
static void app_rearm_network_announcement(void) {}
typedef enum { BSEED_BUDGET_CONSUMED, BSEED_BUDGET_EXHAUSTED,
               BSEED_BUDGET_STORAGE_ERROR } bseed_budget_result_t;
static bseed_budget_result_t bseed_router_rescue_budget_try_consume(void) {
    if(budget_used>=2u) return BSEED_BUDGET_EXHAUSTED;
    budget_used++;
    return BSEED_BUDGET_CONSUMED;
}
static bool bseed_recovery_diag_record(bseed_rescue_reason_t reason,
                                        uint8_t state) {
    assert(reason==BSEED_RESCUE_REASON_FACTORY_STEERING);
    (void)state; return true;
}
static void schedule_reboot(uint16_t delay) {
    assert(delay==2000u); rescue_calls++;
}
static hal_zigbee_network_status_t hal_zigbee_get_network_status(void) {
    return joined ? HAL_ZIGBEE_NETWORK_JOINED : HAL_ZIGBEE_NETWORK_NOT_JOINED;
}
"""
    cases = r"""
int main(void) {
    /* Accepted A then terminal failure. A second B cannot be handed the
     * radio while a duplicate terminal A callback remains possible. */
    hal_zigbee_start_network_steering();
    assert(steer_calls==1u && network_sdk_steering_started_once);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_STEERING);
    bdb_commissioning_callback(BDB_COMMISSION_STA_NO_SCAN_RESPONSE,0);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
    assert(network_steering_terminal_seen);
    now+=5000u; hal_zigbee_start_network_steering();
    assert(steer_calls==1u && rescue_calls==0u);
    bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,0); /* duplicate A */
    now+=10000u; hal_zigbee_start_network_steering();
    assert(steer_calls==1u);
    now+=FACTORY_STEERING_TERMINAL_HOLD_MS+15u*60u*1000u+1u;
    hal_zigbee_start_network_steering();
    assert(steer_calls==1u && rescue_calls==1u && budget_used==1u);
    hal_zigbee_start_network_steering();
    assert(rescue_calls==1u); /* checkpoint only once in this boot */

    /* A clean software boot is a quiescent owner boundary, not a new scan
     * launched from the previous A's terminal callback. */
    network_sdk_steering_started_once=false;
    network_steering_checkpoint_scheduled=false;
    network_steering_terminal_seen=false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    network_start_retry_pending=false;
    now+=100u;
    hal_zigbee_start_network_steering();
    assert(steer_calls==2u);

    /* Synchronous terminal callback cannot be overwritten by STEERING
     * state after the SDK returns. */
    network_sdk_steering_started_once=false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    network_start_retry_pending=false;
    sync_terminal=true;
    sync_terminal_status=BDB_COMMISSION_STA_NO_NETWORK;
    hal_zigbee_start_network_steering();
    assert(steer_calls==3u && network_steering_terminal_seen);
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
    hal_zigbee_start_network_steering();
    assert(steer_calls==3u);

    /* Pure synchronous request rejection did NOT acquire an SDK owner:
     * the 5-second gate allows a new attempt without a reboot. */
    network_sdk_steering_started_once=false;
    network_recovery_state=TELINK_NETWORK_RECOVERY_IDLE;
    network_start_retry_pending=false;
    sync_terminal=false; reject_steer=true;
    hal_zigbee_start_network_steering();
    assert(steer_calls==4u && !network_sdk_steering_started_once);
    now+=5000u; reject_steer=false;
    hal_zigbee_start_network_steering();
    assert(steer_calls==5u && network_sdk_steering_started_once);

    /* Valid exhausted persistent budget: fail closed, no infinite restart
     * storm and no unsafe repeated steering A/B. Supervised reboot remains
     * a separate physical intervention after the automatic cap. */
    budget_used=2u;
    now+=FACTORY_STEERING_OWNER_HOLD_MS+15u*60u*1000u+1u;
    hal_zigbee_start_network_steering();
    assert(steer_calls==5u && rescue_calls==1u && budget_used==2u);
    return 0;
}
"""
    path=tmp_path/"factory_steering_owner.c"
    binary=tmp_path/"factory_steering_owner"
    path.write_text(prelude+defines+"\n"+state+rejoin+callback+ensure+cases,
                    encoding="utf-8")
    built=subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror",
                          "-Wno-unused-parameter","-Wno-unused-function",
                          "-Wno-unused-variable","-I",str(ROOT/"src"),
                          str(path),"-o",str(binary)],
                         capture_output=True,text=True,timeout=30)
    assert built.returncode==0,built.stdout+built.stderr
    ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
    assert ran.returncode==0,ran.stdout+ran.stderr
