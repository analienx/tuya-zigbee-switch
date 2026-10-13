"""Exercise boot classification, exhausted migration and real energy checkpoints."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('role_flags', [[], ['-DEND_DEVICE=1', '-DBSEED_MAINS_CLIENT=1']])
def test_legacy_baseline_survives_migration_fallback_and_reboot(tmp_path, role_flags):
    code = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/metering_cluster.c"
#include "device_config/pm_legacy_migration.c"
device_config_str_t device_config_str;
static uint64_t legacy = 128, unified;
static uint32_t clock_ms, sample;
static int unified_status = HAL_NVM_NOT_FOUND, legacy_status = HAL_NVM_SUCCESS;
static int fault, writes, legacy_reads;
static const char *identity = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;";
uint32_t hal_millis(void) { return clock_ms; }
void device_config_read_raw_from_nv(void) {
    device_config_str.size = strlen(identity);
    memcpy(device_config_str.data, identity, device_config_str.size);
}
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (item == NV_ITEM_PM_MIGRATION_ATTEMPTS) {
        uint32_t attempts = 3; memcpy(data, &attempts, size); return HAL_NVM_SUCCESS;
    }
    if (item == 40) {
        legacy_reads++; memcpy(data, &legacy, size); return legacy_status;
    }
    if (item == 64) {
        memcpy(data, &unified, size);
        if (fault == 3 && unified_status == HAL_NVM_SUCCESS) data[0] ^= 1;
        return fault == 2 ? HAL_NVM_ERROR : unified_status;
    }
    return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == 64 && size == 8); writes++;
    if (fault == 1) return HAL_NVM_ERROR;
    memcpy(&unified, data, size); unified_status = HAL_NVM_SUCCESS; return HAL_NVM_SUCCESS;
}
static void get_data(void *ctx, energy_meter_data_t *data) {
    data->valid = 1; data->energy = sample;
}
static void boot(metering_cluster_t *cluster, energy_meter_t *meter) {
    /* Simulated reset clears RAM, preserving only the NVM fake. */
    legacy_energy_restore_allowed = false; sample = 0; clock_ms = 0;
    assert(migrate_legacy_bseed_pm_nvm());
    metering_cluster_init(cluster, meter); cluster->endpoint = 1;
    g_metering_cluster = cluster; metering_cluster_load_energy(cluster);
}
int main(void) {
    energy_meter_ops_t ops = {.get_data = get_data};
    energy_meter_t meter = {.ops = &ops}; metering_cluster_t cluster;
    boot(&cluster, &meter);
    assert(cluster.energy_baseline_valid && cluster.current_summation_delivered == 128);
    assert(cluster.persistence_retry_pending && writes == 0);
    sample = 7; metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 135);
    fault = 1; assert(!metering_cluster_checkpoint());
    assert(unified_status == HAL_NVM_NOT_FOUND && legacy == 128);
    fault = 0; sample = 10; assert(metering_cluster_checkpoint());
    assert(unified == 138 && legacy == 128);
    boot(&cluster, &meter);
    assert(cluster.current_summation_delivered == 138);
    sample = 2; metering_cluster_update(&cluster);
    fault = 3; assert(!metering_cluster_save_energy(&cluster));
    assert(cluster.persistence_retry_pending && cluster.last_persisted_energy == 138);
    fault = 0; assert(metering_cluster_checkpoint());
    assert(unified == 140);
    metering_cluster_reset_energy(&cluster); assert(unified == 0);
    boot(&cluster, &meter); assert(cluster.current_summation_delivered == 0);
    assert(legacy == 128); /* a legitimate reset is never replaced with max(old,new) */

    unified_status = HAL_NVM_NOT_FOUND; legacy_status = HAL_NVM_ERROR;
    boot(&cluster, &meter); int before = writes;
    assert(!cluster.energy_baseline_valid);
    sample = 9; clock_ms = 600000; metering_cluster_update(&cluster);
    assert(!metering_cluster_checkpoint() && writes == before);
    legacy_status = HAL_NVM_SUCCESS; legacy = 0xffffffffffffULL;
    boot(&cluster, &meter); assert(!cluster.energy_baseline_valid);
    assert(!metering_cluster_checkpoint());
    legacy = 128; legacy_status = HAL_NVM_NOT_FOUND;
    boot(&cluster, &meter); assert(cluster.energy_baseline_valid);
    assert(cluster.current_summation_delivered == 0); /* truly new meter */

    legacy_status = HAL_NVM_SUCCESS; identity = "other;TS011F-BS;";
    before = legacy_reads; boot(&cluster, &meter);
    assert(cluster.current_summation_delivered == 0 && legacy_reads == before);
    return 0;
}
'''
    binary = tmp_path / 'ota-retention'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DBSEED_PM_B28WRPVX=1',
                    *role_flags, '-ffunction-sections', '-fdata-sections',
                    '-Wl,--gc-sections', '-I', str(ROOT / 'src'), '-x', 'c', '-',
                    '-o', str(binary)], input=code, text=True, capture_output=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
