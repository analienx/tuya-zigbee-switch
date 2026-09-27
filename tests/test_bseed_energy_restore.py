"""Energy restore must distinguish absent storage from read failure."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_energy_read_error_never_overwrites_last_good_total(tmp_path):
    code = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "zigbee/metering_cluster.c"

static uint64_t stored = 1234;
static int read_mode = HAL_NVM_ERROR;
static int writes;
static uint32_t sample;
static uint32_t clock_ms = 600000;

uint32_t hal_millis(void) { return clock_ms; }

hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_ENERGY_ACCUMULATION(1));
    assert(size == sizeof(uint64_t));
    if (read_mode == HAL_NVM_ERROR) return HAL_NVM_ERROR;
    if (read_mode == HAL_NVM_NOT_FOUND) return HAL_NVM_NOT_FOUND;
    memcpy(data, &stored, sizeof(stored));
    return HAL_NVM_SUCCESS;
}

hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_ENERGY_ACCUMULATION(1));
    assert(size == sizeof(uint64_t));
    writes++;
    memcpy(&stored, data, sizeof(stored));
    return HAL_NVM_SUCCESS;
}

static void get_data(void *ctx, energy_meter_data_t *data) {
    (void)ctx;
    memset(data, 0, sizeof(*data));
    data->valid = 1;
    data->energy = sample;
}

int main(void) {
    energy_meter_ops_t ops = {.get_data = get_data};
    energy_meter_t meter = {.ops = &ops};
    metering_cluster_t cluster;

    metering_cluster_init(&cluster, &meter);
    cluster.endpoint = 1;
    g_metering_cluster = &cluster;

    /* Storage error: baseline stays unknown and every save/checkpoint is barred. */
    metering_cluster_load_energy(&cluster);
    assert(cluster.energy_baseline_valid == 0);
    metering_cluster_save_energy(&cluster);
    assert(writes == 0 && stored == 1234);
    sample = 9;
    metering_cluster_update(&cluster);
    assert(cluster.energy_baseline_valid == 0);
    assert(writes == 0 && stored == 1234);
    assert(metering_cluster_checkpoint() == false);
    assert(writes == 0 && stored == 1234);

    /* Retry succeeds: restore last good total before accumulating/saving. */
    read_mode = HAL_NVM_SUCCESS;
    sample = 9;
    clock_ms += METERING_BASELINE_RETRY_INTERVAL_MS;
    metering_cluster_update(&cluster);
    assert(cluster.energy_baseline_valid == 1);
    assert(cluster.current_summation_delivered == 1234);
    sample = 18;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 1243);
    metering_cluster_save_energy(&cluster);
    assert(writes >= 1 && stored == 1243);

    /* NOT_FOUND is a genuinely new record and may start at zero. */
    metering_cluster_init(&cluster, &meter);
    cluster.endpoint = 1;
    read_mode = HAL_NVM_NOT_FOUND;
    metering_cluster_load_energy(&cluster);
    assert(cluster.energy_baseline_valid == 1);
    assert(cluster.current_summation_delivered == 0);

    /* Explicit user reset may intentionally replace an unknown baseline. */
    metering_cluster_init(&cluster, &meter);
    cluster.endpoint = 1;
    read_mode = HAL_NVM_ERROR;
    metering_cluster_load_energy(&cluster);
    assert(cluster.energy_baseline_valid == 0);
    metering_cluster_reset_energy(&cluster);
    assert(cluster.energy_baseline_valid == 1);
    assert(stored == 0);
    return 0;
}
'''
    binary = tmp_path / 'energy-restore'
    subprocess.run(
        ['cc', '-std=c99', '-DHAL_STUB', '-DBSEED_PM_B28WRPVX=1',
         '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
         '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
        input=code, text=True, capture_output=True, check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=5)
