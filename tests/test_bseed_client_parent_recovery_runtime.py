"""Execute real Client recovery/AF functions with the pinned SDK's contracts.

The SDK fake clears JOINED on accepted rejoin and owns retries until success;
application callbacks can be absent, delayed or synchronous. No RF is simulated.
"""
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def span(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


@pytest.fixture(scope='module', params=['pm', 'nonpm'])
def recovery_binary(request, tmp_path_factory):
    source = (ROOT / 'src/telink/hal/zigbee_network.c').read_text()
    poll = source.split('#ifdef BSEED_MAINS_CLIENT', 1)[1].split('#endif', 1)[0]
    state = span(source, 'typedef enum {', '// Telink ZDO callbacks')
    recovery = span(source, 'static bool start_rejoin_with_backoff(void)',
                    'void zdo_leave_indication_callback')
    callback = span(source, 'void bdb_commissioning_callback(u8 status, void *arg) {',
                    'void bdb_identify_callback')
    status = span(source, 'hal_zigbee_network_status_t hal_zigbee_get_network_status(void)',
                  'void hal_register_on_network_status_change_callback')
    ensure = span(source, 'void hal_zigbee_start_network_steering(void)',
                  'hal_zigbee_status_t hal_zigbee_send_announce')
    af = span((ROOT / 'src/telink/hal/zigbee_zcl.c').read_text(),
              'static void af_rx_callback(void *arg) {',
              'void telink_zigbee_hal_zcl_init')
    constants = sorted(set(re.findall(r'case (BDB_COMMISSION_STA_\w+)', callback)))
    defines = '\n'.join(f'#define {name} {i}' for i, name in enumerate(constants))
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hal/firmware_health.h"
typedef uint8_t u8;
typedef struct {void (*handler)(void *); void *arg;} hal_task_t;
typedef enum {HAL_ZIGBEE_NETWORK_NOT_JOINED, HAL_ZIGBEE_NETWORK_JOINED,
              HAL_ZIGBEE_NETWORK_JOINING} hal_zigbee_network_status_t;
typedef void (*hal_network_status_change_callback_t)(hal_zigbee_network_status_t);
hal_zigbee_network_status_t hal_zigbee_get_network_status(void);
void bdb_commissioning_callback(u8 status,void *arg);
#define BSEED_MAINS_CLIENT 1
#define RET_OK 0
#define RESPONSE_POLL_RATE 250u
#define MAC_SUCCESS 0
#define MAC_STA_NO_DATA 0xeb
#define REJOIN_SECURITY 1
#define OTA_QUERY_INTERVAL 1u
static uint32_t now, rate=60000, calls, retries, delay, af_calls;
static bool joined=true, factory_new, sdk_owned, reject, reject_disconnected, sync_success;
static struct {u8 scanDuration;} g_bdbAttrs;
static struct {u8 extAddress[8];} g_zbMacPib;
firmware_health_t firmware_health;
uint32_t hal_millis(void) {return now;}
static bool zb_isDeviceJoinedNwk(void) {return joined;}
static bool zb_isDeviceFactoryNew(void) {return factory_new;}
static u8 zb_setPollRate(uint32_t ms) {rate=ms;return RET_OK;}
uint32_t hal_zigbee_get_poll_rate_ms(void) {return rate;}
static void hal_tasks_schedule(hal_task_t *task,uint32_t ms) {delay=ms;}
static uint32_t zb_apsChannelMaskGet(void) {return 0x07fff800;}
static u8 zb_rejoinReqWithBackOff(uint32_t mask,u8 duration);
static u8 bdb_networkSteerStart(void) {assert(factory_new);return RET_OK;}
static void zb_rejoinSecModeSet(u8 mode) {assert(mode==REJOIN_SECURITY);}
static void telink_zigbee_hal_request_ota_query(void) {}
static void ota_queryStart(uint16_t seconds) {}
/* Join-epoch announce re-arm from the Router-family merge; no-op here. */
static void app_rearm_network_announcement(void) {}
typedef struct {void *asdu; unsigned asduLen;
    struct {unsigned dst_ep,cluster_id,dst_addr_mode;} indInfo;} apsdeDataInd_t;
static void (*zcl_activity_callback)(void);
static void *hal_endpoints;
static unsigned hal_endpoints_cnt;
static bool zcl_write_string_gate_allows(void *data,unsigned size,unsigned ep,
    unsigned cluster,void *endpoints,unsigned count) {return true;}
static void ev_buf_free(void *arg) {assert(0);}
static void zcl_rx_handler(void *arg) {af_calls++;}
'''
    sdk = r'''
static u8 zb_rejoinReqWithBackOff(uint32_t mask,u8 duration) {
    calls++; assert(!sdk_owned); /* no overlapping application starts */
    assert(network_recovery_state==TELINK_NETWORK_RECOVERY_REJOIN);
    if(reject) return 0x80;
    if(reject_disconnected) {joined=false;return 0x8a;} /* injected post-mutation error */
    sdk_owned=true; joined=false; /* pinned native ZDO contract */
    if(sync_success) {
        sdk_owned=false;joined=true;
        bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL);
    }
    return RET_OK;
}
static void failed_polls(unsigned n) {
    while(n--) client_health_poll_confirmation(0xe9);
}
static void stale_parent(void) {
    now=CLIENT_PARENT_SILENCE_MS+CLIENT_RECOVERY_STAGGER_MS;
    failed_polls(4); keepalive_verify_handler(NULL);
}
static void sdk_retry_tick(void) {if(sdk_owned) retries++;}
int main(int argc,char **argv) {
    assert(argc==2); client_health_join_epoch();
    if(!strcmp(argv[1],"lost-completion")) {
        stale_parent(); assert(calls==1 && sdk_owned && !joined);
        for(unsigned i=0;i<60;i++) {
            now+=60000; sdk_retry_tick();keepalive_verify_handler(NULL);
            hal_zigbee_start_network_steering();
        }
        assert(retries==60 && calls==1);
        assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINING);
        sdk_owned=false;joined=true;rate=250; /* success; BDB callback lost */
        assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINED);
        assert(rate==60000 && client_poll_failure_streak==0);
        assert(client_health_last_good_ms==now && firmware_health.rejoin_success==1);
        bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL); /* late */
        hal_zigbee_get_network_status(); assert(firmware_health.rejoin_success==1);
    } else if(!strcmp(argv[1],"inbound-does-not-mask")) {
        apsdeDataInd_t frame={0};
        for(unsigned i=0;i<15;i++) {
            now+=60000;frame.indInfo.dst_addr_mode=i%3;
            af_rx_callback(&frame);failed_polls(1);keepalive_verify_handler(NULL);
        }
        assert(af_calls==15 && calls==1 && sdk_owned);
    } else if(!strcmp(argv[1],"healthy-quiet")) {
        for(unsigned i=0;i<60;i++) {
            now+=60000; client_health_poll_confirmation(i%2?MAC_SUCCESS:MAC_STA_NO_DATA);
            keepalive_verify_handler(NULL);
        }
        assert(calls==0);
        now+=3600000;keepalive_verify_handler(NULL);assert(calls==0);
    } else if(!strcmp(argv[1],"callback-failures")) {
        stale_parent();
        for(unsigned i=0;i<10;i++) {
            now+=60000;bdb_commissioning_callback(BDB_COMMISSION_STA_REJOIN_FAILURE,NULL);
            bdb_commissioning_callback(BDB_COMMISSION_STA_PARENT_LOST,NULL);
            hal_zigbee_start_network_steering();
        }
        assert(calls==1 && sdk_owned && firmware_health.rejoin_success==0);
        sdk_owned=false;joined=true;bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL);
        bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL);
        assert(firmware_health.rejoin_start==1 && firmware_health.rejoin_success==1);
    } else if(!strcmp(argv[1],"rejected-cooldown")) {
        reject=true;stale_parent();assert(calls==1 && !sdk_owned);
        assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
        uint32_t first=now;
        now=first+CLIENT_HEALTH_REJOIN_COOLDOWN_MS-1;
        keepalive_verify_handler(NULL);assert(calls==1);
        now++;reject=false;keepalive_verify_handler(NULL);assert(calls==2 && sdk_owned);
        assert(firmware_health.rejoin_start==2 && firmware_health.rejoin_failure==1);
    } else if(!strcmp(argv[1],"rejected-after-disconnect")) {
        reject_disconnected=true;stale_parent();assert(calls==1 && !joined && !sdk_owned);
        uint32_t first=now;
        assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_NOT_JOINED);
        now=first+4999;hal_zigbee_start_network_steering();assert(calls==1);
        now++;reject_disconnected=false;hal_zigbee_start_network_steering();
        assert(calls==2 && sdk_owned && firmware_health.rejoin_failure==1);
    } else if(!strcmp(argv[1],"unrelated-terminal-status")) {
        stale_parent();
        bdb_commissioning_callback(BDB_COMMISSION_STA_NO_NETWORK,NULL);
        bdb_commissioning_callback(BDB_COMMISSION_STA_NO_SCAN_RESPONSE,NULL);
        bdb_commissioning_callback(BDB_COMMISSION_STA_IN_PROGRESS,NULL);
        hal_zigbee_start_network_steering();assert(calls==1 && sdk_owned);
        assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINING);
    } else if(!strcmp(argv[1],"second-parent-loss")) {
        stale_parent();sdk_owned=false;joined=true;
        bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL);
        now+=CLIENT_HEALTH_REJOIN_COOLDOWN_MS;failed_polls(4);
        keepalive_verify_handler(NULL);assert(calls==2 && sdk_owned);
        sdk_owned=false;joined=true;hal_zigbee_get_network_status();
        assert(firmware_health.rejoin_start==2 && firmware_health.rejoin_success==2);
    } else if(!strcmp(argv[1],"ota-entry-exit")) {
        hal_zigbee_set_ota_poll_active(true);assert(rate==250);
        stale_parent();assert(calls==0);
        hal_zigbee_set_ota_poll_active(false);keepalive_verify_handler(NULL);
        assert(calls==1 && sdk_owned);
        hal_zigbee_set_ota_poll_active(true);now+=3600000;
        keepalive_verify_handler(NULL);hal_zigbee_start_network_steering();assert(calls==1);
        joined=true;sdk_owned=false;hal_zigbee_get_network_status();assert(rate==250);
        hal_zigbee_set_ota_poll_active(false);assert(rate==60000);
    } else if(!strcmp(argv[1],"synchronous-success")) {
        sync_success=true;stale_parent();assert(calls==1 && joined && !sdk_owned);
        assert(network_recovery_state==TELINK_NETWORK_RECOVERY_IDLE);
        assert(firmware_health.rejoin_start==1 && firmware_health.rejoin_success==1);
    } else if(!strcmp(argv[1],"wrap-and-boundary")) {
        now=UINT32_MAX-1000;client_health_join_epoch();
        uint32_t start=now, limit=CLIENT_PARENT_SILENCE_MS+client_health_stagger_ms();
        failed_polls(4);now=start+limit-1;keepalive_verify_handler(NULL);
        assert(calls==0 && delay==1);now++;keepalive_verify_handler(NULL);assert(calls==1);
    } else if(!strcmp(argv[1],"jitter")) {
        uint32_t values[256];unsigned distinct=0, upper=0;
        for(unsigned i=0;i<256;i++) {
            g_zbMacPib.extAddress[7]=0xa4;g_zbMacPib.extAddress[1]=i;
            values[i]=client_health_stagger_ms();assert(values[i]<120000);
            if(values[i]>=65536) upper++;
            bool seen=false;for(unsigned j=0;j<i;j++) if(values[j]==values[i]) seen=true;
            if(!seen) distinct++;
        }
        assert(distinct>240 && upper>60);
        uint32_t original=client_health_stagger_ms();g_zbMacPib.extAddress[4]^=1;
        assert(client_health_stagger_ms()!=original);
        now=0;client_health_join_epoch();failed_polls(4);
        uint32_t limit=CLIENT_PARENT_SILENCE_MS+client_health_stagger_ms();
        now=limit-123;keepalive_verify_handler(NULL);assert(calls==0 && delay==123);
        now+=delay;keepalive_verify_handler(NULL);assert(calls==1);
    } else if(!strcmp(argv[1],"factory-new")) {
        factory_new=true;stale_parent();assert(calls==0 && !sdk_owned);
    } else assert(0);
    return 0;
}
'''
    binary = tmp_path_factory.mktemp(f'parent-{request.param}') / 'recovery'
    flags = ['-DBSEED_PM_B28WRPVX=1'] if request.param == 'pm' else []
    subprocess.run(['cc', '-std=c99', *flags, '-I', str(ROOT / 'src'),
                    '-x', 'c', '-', '-o', str(binary)],
                   input=harness + defines + '\n' + poll + state + recovery + callback
                   + status + ensure + af + sdk,
                   text=True, capture_output=True, check=True)
    return binary


@pytest.mark.parametrize('case', [
    'lost-completion', 'inbound-does-not-mask', 'healthy-quiet', 'callback-failures',
    'rejected-cooldown', 'ota-entry-exit', 'synchronous-success',
    'wrap-and-boundary', 'jitter', 'factory-new',
    'rejected-after-disconnect', 'unrelated-terminal-status', 'second-parent-loss',
])
def test_client_parent_recovery_contract(recovery_binary, case):
    subprocess.run([str(recovery_binary), case], check=True, timeout=10)
