"""Execute real metering/checkpoint and reboot handlers with fault-injected NVM."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('role_flags', [[], ['-DEND_DEVICE=1', '-DBSEED_MAINS_CLIENT=1']])
def test_energy_survives_controlled_reboots_and_failure_defers_apply(tmp_path, role_flags):
    ota = (ROOT / 'src/telink/hal/zigbee_ota.c').read_text()
    ota_hook = ota.split('static hal_task_t ota_checkpoint_reboot_task;', 1)[1].split('#ifdef', 1)[0]
    app = (ROOT / 'src/app.c').read_text()
    app_hook = 'bool app_prepare_reboot(void)' + app.split('bool app_prepare_reboot(void)', 1)[1].split('void app_task', 1)[0]
    code = r'''
#include <assert.h>
#include <setjmp.h>
#include <string.h>
#include "zigbee/metering_cluster.c"
#include "device_config/reset.c"
static uint64_t stored = 1000;
static uint32_t sample, clock_ms = 10000, delay_ms;
static int fault, reboots, network_resets;
static jmp_buf reboot_target;
static hal_task_t ota_checkpoint_reboot_task;
uint32_t hal_millis(void) { return clock_ms; }
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_ENERGY_ACCUMULATION(1) && size == 8);
    if (fault == 1) return HAL_NVM_ERROR;
    memcpy(&stored, data, 8);
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (fault == 2) return HAL_NVM_ERROR;
    uint64_t value = stored + (fault == 3);
    memcpy(data, &value, 8);
    return HAL_NVM_SUCCESS;
}
void hal_tasks_schedule(hal_task_t *task, uint32_t delay) { delay_ms = delay; }
void hal_tasks_init(hal_task_t *task) {}
void hal_system_reset(void) { reboots++; longjmp(reboot_target, 1); }
void hal_factory_reset(void) { network_resets++; }
static void ota_mcuReboot(void) { hal_system_reset(); }
void energy_monitoring_tick(void) { if (g_metering_cluster) metering_cluster_update(g_metering_cluster); }
static void get_data(void *ctx, energy_meter_data_t *data) { data->valid = 1; data->energy = sample; }
'''
    code += app_hook + ota_hook + r'''
int main(void) {
    assert(metering_cluster_checkpoint()); /* no PM hardware / early init */
    energy_meter_ops_t ops = {.get_data = get_data};
    energy_meter_t meter = {.ops = &ops};
    metering_cluster_t cluster;
    metering_cluster_init(&cluster, &meter);
    cluster.endpoint = 1;
    g_metering_cluster = &cluster;
    metering_cluster_load_energy(&cluster);
    sample = 12;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 1012 && stored == 1000);
    for (fault = 1; fault <= 3; fault++) {
        ota_reboot_after_checkpoint(0);
        assert(reboots == 0 && delay_ms == 5000);
        reboot_handler(0);
        assert(reboots == 0 && delay_ms == 5000);
        network_reset_handler(0);
        assert(network_resets == 0 && delay_ms == 5000);
    }
    fault = 0;
    sample = 15; /* includes accumulation while retries were pending */
    if (setjmp(reboot_target) == 0) ota_reboot_after_checkpoint(0);
    assert(reboots == 1 && stored == 1015);
    sample = 0;
    metering_cluster_init(&cluster, &meter);
    cluster.endpoint = 1;
    metering_cluster_load_energy(&cluster);
    assert(cluster.current_summation_delivered == 1015);
    sample = 7;
    if (setjmp(reboot_target) == 0) reboot_handler(0);
    assert(reboots == 2 && stored == 1022);
    sample = 10;
    network_reset_handler(0);
    assert(network_resets == 1 && stored == 1025);
    return 0;
}
'''
    binary = tmp_path / 'reboot-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DBSEED_PM_B28WRPVX=1',
                    *role_flags, '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, capture_output=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    assert 'ota_reboot_after_checkpoint(NULL);' in ota
    assert 'ota_checkpoint_reboot_task.handler = ota_reboot_after_checkpoint;' in ota
