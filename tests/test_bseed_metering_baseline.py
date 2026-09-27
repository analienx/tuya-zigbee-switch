"""Energy baseline: NOT_FOUND seeds once, errors report untrusted and retry rarely."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/metering_cluster.c"

static uint32_t fake_now;
uint32_t hal_millis(void) { return fake_now; }

static hal_nvm_status_t nvm_read_status;
static uint64_t nvm_stored_wh;
static unsigned nvm_reads, nvm_writes;
static uint64_t nvm_last_written;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (item != NV_ITEM_ENERGY_ACCUMULATION(2)) {
        return HAL_NVM_NOT_FOUND;
    }
    nvm_reads++;
    if (nvm_read_status != HAL_NVM_SUCCESS) {
        return nvm_read_status;
    }
    assert(size == 8);
    memcpy(data, &nvm_stored_wh, 8);
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    assert(item == NV_ITEM_ENERGY_ACCUMULATION(2) && size == 8);
    nvm_writes++;
    memcpy(&nvm_last_written, data, 8);
    nvm_stored_wh = nvm_last_written;
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}

static uint32_t meter_energy;
static uint8_t meter_valid;
static void fake_get_data(void *ctx, energy_meter_data_t *data) {
    (void)ctx;
    memset(data, 0, sizeof *data);
    data->energy = meter_energy;
    data->valid = meter_valid;
}
static void fake_reset_energy(void *ctx) { (void)ctx; meter_energy = 0; }
static const energy_meter_ops_t fake_ops = {
    .get_data = fake_get_data, .reset_energy = fake_reset_energy,
};
static energy_meter_t fake_meter = {&fake_ops, 0};

static metering_cluster_t cluster;

static void boot(uint8_t endpoint) {
    memset(&cluster, 0, sizeof cluster);
    metering_cluster_init(&cluster, &fake_meter);
    hal_zigbee_cluster slots[1];
    hal_zigbee_endpoint ep;
    memset(&ep, 0, sizeof ep);
    ep.endpoint = endpoint;
    ep.clusters = slots;
    metering_cluster_add_to_endpoint(&cluster, &ep);
}

int main(void) {
    /* No cluster registered: checkpoint is trivially satisfied. */
    assert(metering_cluster_checkpoint() == true);
    /* S1: NOT_FOUND seeds one default; later ticks never re-read. */
    nvm_read_status = HAL_NVM_NOT_FOUND;
    meter_energy = 5000;
    meter_valid = 1;
    fake_now = 0;
    nvm_reads = 0;
    nvm_writes = 0;
    boot(2);
    assert(nvm_reads == 1);
    assert(cluster.current_summation_delivered == 0);
    assert(cluster.energy_baseline_valid == 1);
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 0);
    assert(cluster.last_energy_value == 5000);
    meter_energy = 5100;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 100);
    assert(nvm_reads == 1 && nvm_writes == 0);
    /* S2: restored totals never double-count a live meter. */
    nvm_read_status = HAL_NVM_SUCCESS;
    nvm_stored_wh = 1000;
    meter_energy = 5000;
    fake_now = 0;
    nvm_reads = 0;
    nvm_writes = 0;
    boot(2);
    assert(cluster.current_summation_delivered == 1000);
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 1000);
    assert(cluster.last_energy_value == 5000);
    meter_energy = 5050;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 1050);
    /* S3: errors report untrusted, save nothing, and retry rarely. */
    nvm_read_status = HAL_NVM_ERROR;
    meter_energy = 7000;
    fake_now = 0;
    nvm_reads = 0;
    nvm_writes = 0;
    boot(2);
    assert(nvm_reads == 1);
    assert(cluster.current_summation_delivered == METERING_SUMMATION_UNTRUSTED);
    assert(cluster.energy_baseline_valid == 0);
    assert(metering_cluster_checkpoint() == false);
    fake_now = 1000;
    metering_cluster_update(&cluster);
    fake_now = 2000;
    metering_cluster_update(&cluster);
    assert(nvm_reads == 2 && nvm_writes == 0);
    assert(cluster.current_summation_delivered == METERING_SUMMATION_UNTRUSTED);
    fake_now = 300000;
    metering_cluster_update(&cluster);
    assert(nvm_reads == 3 && nvm_writes == 0);
    /* Recovery restores NVM, drops the unmeasurable outage, then resumes. */
    nvm_read_status = HAL_NVM_SUCCESS;
    nvm_stored_wh = 2000;
    meter_energy = 8000;
    fake_now = 600000;
    metering_cluster_update(&cluster);
    assert(cluster.energy_baseline_valid == 1);
    assert(cluster.current_summation_delivered == 2000);
    assert(cluster.last_energy_value == 8000);
    assert(nvm_writes == 0);
    meter_energy = 8100;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 2100);
    assert(nvm_writes == 1 && nvm_last_written == 2100);
    assert(metering_cluster_checkpoint() == true);
    /* S4: explicit reset replaces even an unknown baseline, once. */
    nvm_read_status = HAL_NVM_ERROR;
    fake_now = 700000;
    nvm_reads = 0;
    nvm_writes = 0;
    boot(2);
    assert(cluster.energy_baseline_valid == 0);
    metering_cluster_reset_energy(&cluster);
    assert(cluster.current_summation_delivered == 0);
    assert(cluster.energy_baseline_valid == 1);
    assert(meter_energy == 0 && nvm_writes == 1 && nvm_last_written == 0);
    meter_energy = 50;
    meter_valid = 1;
    metering_cluster_update(&cluster);
    assert(cluster.current_summation_delivered == 50);
    return 0;
}
'''


def test_energy_baseline_status_trichotomy(tmp_path):
    binary = tmp_path / 'metering-baseline-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=CODE, text=True, capture_output=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    source = (ROOT / 'src/zigbee/metering_cluster.c').read_text()
    assert 'METERING_SUMMATION_UNTRUSTED' in source
    assert 'METERING_BASELINE_RETRY_INTERVAL_MS' in source
    assert 'last_energy_value_seeded' in source
