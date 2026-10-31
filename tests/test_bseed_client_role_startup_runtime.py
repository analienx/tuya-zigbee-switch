"""Run actual Client startup and role handling against NVM/task fault injection."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def span(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


@pytest.fixture(scope='module', params=['pm', 'nonpm'])
def startup_binary(request, tmp_path_factory):
    source = (ROOT / 'src/app.c').read_text()
    globals_ = span(source, '#define CLIENT_ROLE_RETRY_LIMIT',
                    'static void apply_mains_client_defaults')
    role = span(source, 'static bool process_device_type_change(void) {', '#else')
    init = span(source, 'void app_init(void) {', '/* Device announces are per join epoch')
    runtime = source[source.index('/* Device announces are per join epoch'):]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "app.h"
#include "hal/tasks.h"
#include "hal/nvm.h"
#include "device_config/device_type.h"
#include "device_config/nvm_items.h"
#define DEVICE_MIGRATION_BLOCK_INIT 1
#define DEFAULT_RESET_DELAY_MS 2000
#define HAL_ZIGBEE_NETWORK_NOT_JOINED 0
#define HAL_ZIGBEE_NETWORK_JOINED 1
#define HAL_ZIGBEE_NETWORK_JOINING 2
#define HAL_ZIGBEE_OK 0
typedef int hal_zigbee_network_status_t;
static enum device_type_t stored_role=DEVICE_TYPE_ROUTER;
static hal_nvm_status_t read_status=HAL_NVM_ERROR, write_status=HAL_NVM_SUCCESS;
static unsigned reads,writes,resets,reboots,parsed,network_calls,meter_calls,checkpoint_calls;
static bool reset_ok=true;
static unsigned reset_failures_left;
static hal_task_t *pending;
static uint32_t pending_delay;
static const uint32_t saved_energy=123456;
hal_nvm_status_t hal_nvm_read(uint8_t item,uint16_t size,uint8_t *data) {
    assert(item==NV_ITEM_DEVICE_TYPE && size==sizeof(stored_role)); reads++;
    if(read_status==HAL_NVM_SUCCESS) memcpy(data,&stored_role,size);
    return read_status;
}
hal_nvm_status_t hal_nvm_write(uint8_t item,uint16_t size,uint8_t *data) {
    assert(item==NV_ITEM_DEVICE_TYPE && size==sizeof(stored_role)); writes++;
    if(write_status==HAL_NVM_SUCCESS) memcpy(&stored_role,data,size);
    return write_status;
}
bool hal_role_change_reset(void) {
    resets++;
    if(reset_failures_left) {reset_failures_left--;return false;}
    return reset_ok;
}
static uint32_t hal_millis(void) {return 0;}
void schedule_reboot(uint16_t ms) {reboots++;}
void hal_tasks_init(hal_task_t *task) {memset(task,0,sizeof(*task));}
void hal_tasks_schedule(hal_task_t *task,uint32_t ms) {pending=task;pending_delay=ms;}
static void handle_version_changes(void) {}
static bool migrate_legacy_bseed_pm_nvm(void) {return true;}
static int handle_device_specific_migrations(void) {return 0;}
static void device_config_enable_parser_preflight(void) {}
static void parse_config(void) {parsed++;}
static void apply_mains_client_defaults(void) {}
static void hal_zigbee_init_ota(void) {network_calls++;}
static void init_global_attr_write_callback(void) {}
static void energy_monitoring_tick(void) {meter_calls++;}
static bool metering_cluster_checkpoint(void) {checkpoint_calls++;return true;}
static void poll_control_cluster_update(void) {}
static int hal_zigbee_get_network_status(void) {network_calls++;return HAL_ZIGBEE_NETWORK_NOT_JOINED;}
static void hal_zigbee_start_network_steering(void) {network_calls++;}
static int hal_zigbee_send_announce(void) {network_calls++;return HAL_ZIGBEE_OK;}
static void init_energy_reporting(void) {network_calls++;}
'''
    cases = r'''
static void blocked_runtime(void) {
    assert(!client_app_initialized);unsigned n=network_calls,m=meter_calls,c=checkpoint_calls;
    app_task();assert(app_prepare_reboot());
    assert(network_calls==n && meter_calls==m && checkpoint_calls==c);
    assert(saved_energy==123456);
}
static void retry(void) {
    assert(pending && pending_delay==5000);hal_task_t *task=pending;pending=NULL;
    task->handler(task->arg);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"persistent-read-error")) {
        app_init();blocked_runtime();
        while(pending) {retry();blocked_runtime();}
        assert(reads==4 && writes==0 && resets==0 && reboots==0 && parsed==0);
        assert(stored_role==DEVICE_TYPE_ROUTER);
    } else if(!strcmp(argv[1],"transient-read-error")) {
        app_init();blocked_runtime();assert(writes==0 && resets==0);
        read_status=HAL_NVM_SUCCESS;retry();blocked_runtime();
        assert(resets==1 && writes==1 && reboots==1 && parsed==0);
        assert(stored_role==CURRENT_DEVICE_TYPE && !pending);
        /* Restored boot after the selective reset, not a second transition. */
        client_role_retries=0;app_init();assert(client_app_initialized && parsed==1);
        app_init();assert(parsed==1 && resets==1); /* initialized startup is idempotent */
    } else if(!strcmp(argv[1],"missing-marker")) {
        read_status=HAL_NVM_NOT_FOUND;app_init();
        assert(client_app_initialized && writes==1 && resets==0 && parsed==1);
        assert(stored_role==CURRENT_DEVICE_TYPE && !pending);
    } else if(!strcmp(argv[1],"missing-marker-write-error")) {
        read_status=HAL_NVM_NOT_FOUND;write_status=HAL_NVM_ERROR;app_init();blocked_runtime();
        assert(writes==1 && resets==0 && parsed==0);
        write_status=HAL_NVM_SUCCESS;retry();assert(client_app_initialized && writes==2);
    } else if(!strcmp(argv[1],"same-role")) {
        stored_role=CURRENT_DEVICE_TYPE;read_status=HAL_NVM_SUCCESS;app_init();
        assert(client_app_initialized && resets==0 && writes==0 && parsed==1);
        app_task();assert(meter_calls==1);app_prepare_reboot();assert(checkpoint_calls==1);
    } else if(!strcmp(argv[1],"role-transition")) {
        read_status=HAL_NVM_SUCCESS;app_init();blocked_runtime();
        assert(resets==1 && writes==1 && reboots==1 && parsed==0);
        assert(stored_role==CURRENT_DEVICE_TYPE);
    } else if(!strcmp(argv[1],"reset-error")) {
        /* Persistent reset failure retries in place, then blocks: no reboot
         * loop repeating the failing selective reset on every boot. */
        read_status=HAL_NVM_SUCCESS;reset_ok=false;app_init();blocked_runtime();
        while(pending) {retry();blocked_runtime();}
        assert(resets==4 && writes==0 && reboots==0 && stored_role==DEVICE_TYPE_ROUTER);
    } else if(!strcmp(argv[1],"transient-reset-error")) {
        read_status=HAL_NVM_SUCCESS;reset_failures_left=1;app_init();blocked_runtime();
        assert(resets==1 && reboots==0 && pending);
        retry();blocked_runtime();
        assert(resets==2 && writes==1 && reboots==1 && stored_role==CURRENT_DEVICE_TYPE);
    } else if(!strcmp(argv[1],"transition-marker-write-error")) {
        read_status=HAL_NVM_SUCCESS;write_status=HAL_NVM_ERROR;app_init();blocked_runtime();
        while(pending) {retry();blocked_runtime();}
        assert(resets==4 && writes==4 && reboots==0 && stored_role==DEVICE_TYPE_ROUTER);
    } else assert(0);
    return 0;
}
'''
    binary = tmp_path_factory.mktemp(f'startup-{request.param}') / 'startup'
    flags = ['-DBSEED_PM_B28WRPVX=1'] if request.param == 'pm' else []
    subprocess.run(['cc', '-std=c99', '-DBSEED_MAINS_CLIENT=1', '-DEND_DEVICE=1',
                    '-DHAL_STUB=1', *flags, '-I', str(ROOT / 'src'),
                    '-x', 'c', '-', '-o', str(binary)],
                   input=harness + globals_ + role + init + runtime + cases,
                   text=True, capture_output=True, check=True)
    return binary


@pytest.mark.parametrize('case', [
    'persistent-read-error', 'transient-read-error', 'missing-marker',
    'missing-marker-write-error', 'same-role', 'role-transition',
    'reset-error', 'transient-reset-error', 'transition-marker-write-error',
])
def test_client_role_startup_contract(startup_binary, case):
    subprocess.run([str(startup_binary), case], check=True, timeout=10)
