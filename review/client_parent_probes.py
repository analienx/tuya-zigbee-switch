"""Review counterexamples for an immutable candidate; no production source edits."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def span(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


def probes(output):
    source = Path('src/telink/hal/zigbee_network.c').read_text()
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
typedef enum {HAL_ZIGBEE_NETWORK_NOT_JOINED,HAL_ZIGBEE_NETWORK_JOINED,HAL_ZIGBEE_NETWORK_JOINING} hal_zigbee_network_status_t;
typedef void (*hal_network_status_change_callback_t)(hal_zigbee_network_status_t);
hal_zigbee_network_status_t hal_zigbee_get_network_status(void);
#define BSEED_MAINS_CLIENT 1
#define BSEED_PM_B28WRPVX 1
#define RET_OK 0
#define RESPONSE_POLL_RATE 250u
#define MAC_SUCCESS 0
#define MAC_STA_NO_DATA 0xeb
#define REJOIN_SECURITY 1
static uint32_t now, rate=60000, one_shots, backoffs, poll_sets, schedule_ms;
static bool joined=true, factory_new, drop_joined;
static struct {u8 scanDuration;} g_bdbAttrs;
static struct {u8 extAddress[8];} g_zbMacPib;
firmware_health_t firmware_health;
uint32_t hal_millis(void) {return now;}
static bool zb_isDeviceJoinedNwk(void) {return joined;}
static bool zb_isDeviceFactoryNew(void) {return factory_new;}
static u8 zb_setPollRate(uint32_t ms) {poll_sets++;rate=ms;return RET_OK;}
uint32_t hal_zigbee_get_poll_rate_ms(void) {return rate;}
static void hal_tasks_schedule(hal_task_t *task,uint32_t ms) {schedule_ms=ms;}
static uint32_t zb_apsChannelMaskGet(void) {return 0x07fff800;}
static u8 zb_rejoinReq(uint32_t mask,u8 duration) {
    one_shots++; if(drop_joined) joined=false; return RET_OK;
}
static u8 zb_rejoinReqWithBackOff(uint32_t mask,u8 duration) {backoffs++;return RET_OK;}
static u8 bdb_networkSteerStart(void) {return RET_OK;}
static void zb_rejoinSecModeSet(u8 mode) {assert(mode==REJOIN_SECURITY);}
static void telink_zigbee_hal_request_ota_query(void) {}
'''
    checks = r'''
static void failed_polls(unsigned count) {
    for(unsigned i=0;i<count;i++) client_health_poll_confirmation(0xe9);
}
int main(int argc,char **argv) {
    assert(argc==2); client_health_join_epoch();
    if(!strcmp(argv[1],"positive-controls")) {
        now=900000; failed_polls(4); ota_fast_poll_active=true;
        client_health_recovery_tick(); assert(one_shots==0);
        ota_fast_poll_active=false; client_health_poll_confirmation(MAC_STA_NO_DATA);
        client_health_recovery_tick(); assert(one_shots==0);
        now+=900000; failed_polls(4); client_health_recovery_tick();
        assert(one_shots==1 && client_health_probe_active);
        printf("{\"positive_controls\":true,\"one_shots\":%u}\n",one_shots);
    } else if(!strcmp(argv[1],"disconnected-lost-completion")) {
        now=900000; drop_joined=true; failed_polls(4); client_health_recovery_tick();
        assert(one_shots==1 && client_health_probe_active && !joined);
        for(unsigned i=0;i<60;i++) {
            now+=60000; keepalive_verify_handler(NULL);
            hal_zigbee_start_network_steering();
        }
        assert(client_health_probe_active && backoffs==0 && one_shots==1);
        assert(hal_zigbee_get_network_status()==HAL_ZIGBEE_NETWORK_JOINING);
        printf("{\"finding\":\"orphan_ownership\",\"age_ms\":%u,\"deadline_ms\":%u,\"backoffs\":%u}\n",now-client_health_last_probe_ms,CLIENT_ONE_SHOT_DEADLINE_MS,backoffs);
    } else if(!strcmp(argv[1],"unrelated-inbound")) {
        for(unsigned i=0;i<60;i++) {
            now+=60000;
            /* AF currently notes ANY accepted inbound ZCL, including broadcasts. */
            telink_zigbee_hal_client_note_inbound(); failed_polls(4);
            keepalive_verify_handler(NULL);
        }
        assert(one_shots==0 && client_poll_failure_streak==4);
        printf("{\"finding\":\"inbound_masks_parent_failures\",\"duration_ms\":%u,\"one_shots\":%u}\n",now,one_shots);
    } else if(!strcmp(argv[1],"one-shot-success-counter")) {
        now=900000; failed_polls(4); client_health_recovery_tick();
        bdb_commissioning_callback(BDB_COMMISSION_STA_SUCCESS,NULL);
        assert(firmware_health.rejoin_start==1 && firmware_health.rejoin_success==0);
        printf("{\"finding\":\"missing_success_metric\",\"starts\":%u,\"successes\":%u}\n",firmware_health.rejoin_start,firmware_health.rejoin_success);
    }
    return 0;
}
'''
    code = harness + defines + '\n' + poll + state + recovery + callback + status + ensure + checks
    (output / 'counterexamples.c').write_text(code)
    binary = output / 'counterexamples'
    subprocess.run(['cc', '-std=c99', '-I', str(Path('src').resolve()), '-x', 'c', '-',
                    '-o', str(binary)], input=code, text=True, check=True)
    evidence = []
    for case in ('positive-controls', 'disconnected-lost-completion',
                 'unrelated-inbound', 'one-shot-success-counter'):
        result = subprocess.run([str(binary), case], text=True, capture_output=True,
                                check=True, timeout=10)
        print(case, result.stdout, flush=True)
        evidence.append({'case': case, 'stdout': result.stdout})
    (output / 'counterexamples.json').write_text(json.dumps({
        'reviewedSha': os.environ['REVIEWED_SHA'], 'hardwareExperiment': False,
        'sdkOperations': 'fakes; actual application state machine functions',
        'cases': evidence}, indent=2))


def extract(folder):
    for role in ('pm', 'nonpm'):
        source = (folder / f'{role}-disassembly.txt').read_text()
        parts = re.split(r'(?=^[0-9a-f]+ <[^>]+>:\s*$)', source, flags=re.M)
        selected = [part for part in parts if re.search(
            r'^\w+ <(?:.*[Pp]oll.*|.*[Rr]ejoin.*|zb_endDeviceSyncReq|zb_isDeviceJoinedNwk|bdb_zdoStartDevCnf)>:',
            part, re.M)]
        (folder / f'{role}-selected-sdk.txt').write_text('\n'.join(selected))


def main():
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Review probes must execute on GitHub-hosted Actions')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--extract', type=Path)
    args = parser.parse_args()
    if args.extract:
        extract(args.extract)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        probes(args.output)


if __name__ == '__main__':
    main()
