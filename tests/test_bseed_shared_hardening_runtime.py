"""Fault injection for shared metering and network hardening."""
from pathlib import Path
import subprocess
import re
import pytest
from tests.test_bseed_metering_baseline import CODE

ROOT = Path(__file__).resolve().parents[1]


def run_c(tmp_path, code, flags=()):
    binary = tmp_path / "hardening-test"
    result = subprocess.run(["cc", "-std=c99", "-DHAL_STUB",
                             "-ffunction-sections", "-fdata-sections",
                             "-Wl,--gc-sections", *flags, "-I", str(ROOT / "src"),
                             "-x", "c", "-", "-o", str(binary)],
                            input=code, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(binary)], check=True, timeout=5)


@pytest.mark.parametrize("flags", [(), ("-DEND_DEVICE=1", "-DBSEED_MAINS_CLIENT=1")])
def test_failed_saves_retry_without_advancing_success_stamp(tmp_path, flags):
    code = CODE.split("int main(void)", 1)[0]
    code = code.replace("static unsigned nvm_reads, nvm_writes;",
                        "static unsigned nvm_reads, nvm_writes; static int fail_write;")
    code = code.replace("nvm_writes++;", "nvm_writes++; if(fail_write) return HAL_NVM_ERROR;")
    run_c(tmp_path, code + r'''
int main(void) {
    nvm_read_status=HAL_NVM_SUCCESS; nvm_stored_wh=100;
    meter_valid=1; boot(2);
    meter_energy=10; fake_now=300000; fail_write=1;
    metering_cluster_update(&cluster);
    assert(nvm_writes==1 && cluster.last_nvm_save_time==0);
    assert(cluster.persistence_failures==1 && nvm_stored_wh==100);
    fake_now=329999; metering_cluster_update(&cluster); assert(nvm_writes==1);
    fake_now=330000; fail_write=0; metering_cluster_update(&cluster);
    assert(nvm_writes==2 && nvm_stored_wh==110);
    assert(cluster.last_nvm_save_time==330000 && !cluster.persistence_retry_pending);
    fake_now=630000; metering_cluster_update(&cluster);
    assert(nvm_writes==2); /* unchanged total causes no flash wear */
    fail_write=1; metering_cluster_reset_energy(&cluster);
    assert(cluster.persistence_retry_pending && nvm_stored_wh==110);
    fake_now+=30000; fail_write=0; meter_valid=0; metering_cluster_update(&cluster);
    assert(nvm_stored_wh==0 && !cluster.persistence_retry_pending);
    return 0;
}
''', flags)


@pytest.mark.parametrize("flags", [(), ("-DEND_DEVICE=1", "-DBSEED_MAINS_CLIENT=1")])
def test_meter_stall_invalidates_and_restarts_with_fresh_window(tmp_path, flags):
    run_c(tmp_path, r'''
#include <assert.h>
#include "base_components/energy_measurement/hlw8012.c"
static uint32_t now, pulses[2], scheduled, delay;
uint32_t hal_millis(void) {return now;}
void hal_tasks_init(hal_task_t *t) {(void)t;}
void hal_tasks_schedule(hal_task_t *t,uint32_t ms) {(void)t; scheduled++; delay=ms;}
hal_gpio_counter_t hal_gpio_counter_init(hal_gpio_pin_t p,hal_gpio_counter_edge_t e,hal_gpio_pull_t l) {return p==1?0:1;}
void hal_gpio_counter_deinit(hal_gpio_counter_t c) {(void)c;}
void hal_gpio_counter_start(hal_gpio_counter_t c) {(void)c;}
void hal_gpio_counter_stop(hal_gpio_counter_t c) {(void)c;}
void hal_gpio_counter_reset(hal_gpio_counter_t c) {pulses[c]=0;}
uint32_t hal_gpio_counter_read(hal_gpio_counter_t c) {return pulses[c];}
void hal_gpio_init(hal_gpio_pin_t p,uint8_t in,hal_gpio_pull_t l) {}
void hal_gpio_set(hal_gpio_pin_t p) {}
void hal_gpio_clear(hal_gpio_pin_t p) {}
int main(void) {
    hlw8012_t dev; assert(!hlw8012_init(&dev,1,2,3));
    now=5000; update_measurement_handler(&dev);
    assert(dev.data.valid); /* zero pulses is a fresh idle sample */
    dev.data.energy=42;
    now=25000; hlw8012_tick(&dev);
    assert(!dev.data.valid && dev.sample_stale && dev.sample_stalls==1 && delay==0);
    uint32_t before=scheduled;
    for(int i=0;i<100;i++) hlw8012_tick(&dev);
    assert(before==scheduled); /* main-loop checks cannot starve the task */
    pulses[0]=1000; update_measurement_handler(&dev);
    assert(dev.data.energy==42 && !dev.data.valid && delay==5000);
    now=30000; pulses[0]=10; update_measurement_handler(&dev);
    assert(dev.data.valid && !dev.sample_stale);
    now=UINT32_MAX-10000; dev.data.last_sample_time=now;
    now+=20000; hlw8012_tick(&dev);
    assert(!dev.data.valid && dev.sample_stalls==2); /* millisecond wrap */
    return 0;
}
''', flags)


@pytest.mark.parametrize("flags", [(), ("-DZB_ED_ROLE=1", "-DBSEED_MAINS_CLIENT=1")])
def test_rejected_recovery_is_paced_and_sdk_backoff_keeps_ownership(tmp_path, flags):
    source = (ROOT / "src/telink/hal/zigbee_network.c").read_text()
    state = "typedef enum {" + source.split("typedef enum {", 1)[1].split("// Telink ZDO callbacks", 1)[0]
    rejoin = "static bool start_rejoin_with_backoff(void)" + source.split(
        "static bool start_rejoin_with_backoff(void)", 1)[1].split(
        "void zdo_leave_indication_callback", 1)[0]
    callback = "void bdb_commissioning_callback(u8 status, void *arg) {" + source.split(
        "void bdb_commissioning_callback(u8 status, void *arg) {", 1)[1].split(
        "void bdb_identify_callback", 1)[0]
    status = "hal_zigbee_network_status_t hal_zigbee_get_network_status(void)" + source.split(
        "hal_zigbee_network_status_t hal_zigbee_get_network_status(void)", 1)[1].split(
        "void hal_register_on_network_status_change_callback", 1)[0]
    ensure = "void hal_zigbee_start_network_steering(void)" + source.split(
        "void hal_zigbee_start_network_steering(void)", 1)[1].split(
        "hal_zigbee_status_t hal_zigbee_send_announce", 1)[0]
    constants = sorted(set(re.findall(r"case (BDB_COMMISSION_STA_\w+)", callback)))
    defines = "\n".join(f"#define {name} {i}" for i, name in enumerate(constants))
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>
#include "hal/firmware_health.h"
typedef uint8_t u8;
#define RET_OK 0
#define BSEED_PM_B28WRPVX 1
firmware_health_t firmware_health;
typedef enum {HAL_ZIGBEE_NETWORK_NOT_JOINED,HAL_ZIGBEE_NETWORK_JOINED,HAL_ZIGBEE_NETWORK_JOINING} hal_zigbee_network_status_t;
typedef void (*hal_network_status_change_callback_t)(hal_zigbee_network_status_t);
hal_zigbee_network_status_t hal_zigbee_get_network_status(void);
static uint32_t now, rejoin_calls, steer_calls;
static bool joined, factory_new;
static u8 fail_start;
static struct {u8 scanDuration;} g_bdbAttrs;
uint32_t hal_millis(void) {return now;}
static bool zb_isDeviceJoinedNwk(void) {return joined;}
static bool zb_isDeviceFactoryNew(void) {return factory_new;}
static uint32_t zb_apsChannelMaskGet(void) {return 0;}
static u8 zb_rejoinReqWithBackOff(uint32_t mask,u8 duration) {rejoin_calls++;return fail_start;}
static u8 bdb_networkSteerStart(void) {steer_calls++;return fail_start;}
static void configure_mains_client_keepalive(void) {}
static void telink_zigbee_hal_request_ota_query(void) {}
'''
    checks = r'''
int main(void) {
    fail_start=1;
    hal_zigbee_start_network_steering();
    assert(rejoin_calls==1);
    for(int i=0;i<1000;i++) hal_zigbee_start_network_steering();
    now=4999; hal_zigbee_start_network_steering(); assert(rejoin_calls==1);
    now=5000; fail_start=0; hal_zigbee_start_network_steering();
    assert(rejoin_calls==2 && hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINING);
    bdb_commissioning_callback(BDB_COMMISSION_STA_REJOIN_FAILURE,0);
    bdb_commissioning_callback(BDB_COMMISSION_STA_PARENT_LOST,0);
    now=60000; hal_zigbee_start_network_steering(); assert(rejoin_calls==2);
    joined=true; assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINED);
    assert(firmware_health.rejoin_success==1);
    joined=false; hal_zigbee_start_network_steering(); assert(rejoin_calls==3);
    joined=true; hal_zigbee_get_network_status(); joined=false;
    factory_new=true; fail_start=1; now=UINT32_MAX-1000;
    hal_zigbee_start_network_steering(); assert(steer_calls==1);
    now+=4999; hal_zigbee_start_network_steering(); assert(steer_calls==1);
    now++; fail_start=0; hal_zigbee_start_network_steering(); assert(steer_calls==2);
    assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINING);
    return 0;
}
'''
    run_c(tmp_path, harness + defines + "\n" + state + rejoin +
          callback + status + ensure + checks, flags)


def test_poll_diagnostics_count_empty_ack_as_success_and_saturate(tmp_path):
    source = (ROOT / "src/telink/hal/zigbee_network.c").read_text()
    callback = "static void zdo_sync_confirmation_callback" + source.split(
        "static void zdo_sync_confirmation_callback", 1)[1].split(
        "zdo_appIndCb_t zdo_callbacks", 1)[0]
    run_c(tmp_path, r'''
#include <assert.h>
#include "hal/firmware_health.h"
#define MAC_SUCCESS 0
#define MAC_STA_NO_DATA 0xEB
typedef struct {uint8_t status;} nlme_sync_cnf_t;
firmware_health_t firmware_health;
''' + callback + r'''
int main(void) {
    nlme_sync_cnf_t cnf={MAC_SUCCESS}; zdo_sync_confirmation_callback(&cnf);
    cnf.status=MAC_STA_NO_DATA; zdo_sync_confirmation_callback(&cnf);
    assert(firmware_health.poll_success==2 && !firmware_health.poll_failure);
    cnf.status=0xE9; zdo_sync_confirmation_callback(&cnf);
    assert(firmware_health.poll_failure==1 && firmware_health.last_poll_status==0xE9);
    firmware_health.poll_failure=UINT32_MAX; zdo_sync_confirmation_callback(&cnf);
    assert(firmware_health.poll_failure==UINT32_MAX);
    return 0;
}
''')
