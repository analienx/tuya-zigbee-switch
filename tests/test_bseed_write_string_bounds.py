"""Foundation-write string bounds: gate drops, calibration restores, blob kept."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

GATE_CODE = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/zcl_write_string_gate.c"

static uint8_t cal_storage[256];
static uint8_t cfg_storage[130];
static uint8_t ro_storage[40];
static uint16_t u16_storage;

static hal_zigbee_attribute elec_attrs[2];
static hal_zigbee_attribute basic_attrs[2];
static hal_zigbee_cluster clusters[2];
static hal_zigbee_endpoint eps[1];

static void setup_tables(void) {
    elec_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
        ZCL_DATA_TYPE_CHAR_STR, ATTR_WRITABLE, sizeof cal_storage, cal_storage};
    elec_attrs[0].write_max_size = 37;
    elec_attrs[1] = (hal_zigbee_attribute){0x0000, ZCL_DATA_TYPE_UINT16,
        ATTR_WRITABLE, sizeof u16_storage, (uint8_t *)&u16_storage};
    basic_attrs[0] = (hal_zigbee_attribute){ZCL_ATTR_BASIC_DEVICE_CONFIG,
        ZCL_DATA_TYPE_LONG_CHAR_STR, ATTR_WRITABLE, sizeof cfg_storage, cfg_storage};
    basic_attrs[1] = (hal_zigbee_attribute){0x0004, ZCL_DATA_TYPE_CHAR_STR,
        ATTR_READONLY, sizeof ro_storage, ro_storage};
    clusters[0] = (hal_zigbee_cluster){ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 1, 2, elec_attrs, 0};
    clusters[1] = (hal_zigbee_cluster){ZCL_CLUSTER_BASIC, 1, 2, basic_attrs, 0};
    eps[0] = (hal_zigbee_endpoint){2, 0, 0, 0, 2, clusters};
}

static int allows(const uint8_t *asdu, uint16_t len, uint16_t cluster) {
    return zcl_write_string_gate_allows(asdu, len, 2, cluster, eps, 1) ? 1 : 0;
}

static uint16_t short_rec(uint8_t *out, uint16_t attr, uint8_t type, uint8_t declared,
                           uint8_t present) {
    out[0] = (uint8_t)attr; out[1] = (uint8_t)(attr >> 8); out[2] = type; out[3] = declared;
    memset(out + 4, 'A', present);
    return (uint16_t)(4 + present);
}

static uint16_t long_rec(uint8_t *out, uint16_t attr, uint16_t declared, uint16_t present) {
    out[0] = (uint8_t)attr; out[1] = (uint8_t)(attr >> 8);
    out[2] = ZCL_DATA_TYPE_LONG_CHAR_STR;
    out[3] = (uint8_t)declared; out[4] = (uint8_t)(declared >> 8);
    memset(out + 5, 'B', present);
    return (uint16_t)(5 + present);
}

int main(void) {
    setup_tables();
    uint8_t msg[512];
    /* Profile write header, no manuf code. */
    msg[0] = 0x00; msg[1] = 0x01; msg[2] = 0x02;
    /* Calibration CHAR_STR: enforce application capacity before the SDK copy,
       even when the backing storage is larger. */
    uint16_t n = 3 + short_rec(msg + 3, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                               ZCL_DATA_TYPE_CHAR_STR, 36, 36);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 1);
    n = 3 + short_rec(msg + 3, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                      ZCL_DATA_TYPE_CHAR_STR, 40, 10);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 0);
    n = 3 + short_rec(msg + 3, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                      ZCL_DATA_TYPE_CHAR_STR, 255, 255);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 0);
    n = 3 + short_rec(msg + 3, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                      ZCL_DATA_TYPE_CHAR_STR, 40, 40);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 0);
    /* Config LONG_CHAR_STR: boundary enforced, oversized dropped. */
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 128, 128);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 1);
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 129, 129);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 300, 300);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 100, 20);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    /* Mixed records: valid UINT16 plus bad string drops everything. */
    uint8_t *p = msg + 3;
    p[0] = 0x00; p[1] = 0x00; p[2] = ZCL_DATA_TYPE_UINT16; p[3] = 0x11; p[4] = 0x22;
    n = 3 + 5 + short_rec(msg + 8, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                          ZCL_DATA_TYPE_CHAR_STR, 40, 10);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 0);
    n = 3 + 5 + short_rec(msg + 8, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                          ZCL_DATA_TYPE_CHAR_STR, 10, 10);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 1);
    /* Cluster-specific Toggle (cmd 0x02) to a registered cluster is not a write. */
    msg[0] = 0x01; msg[1] = 0x01; msg[2] = 0x02;
    assert(allows(msg, 3, ZCL_CLUSTER_BASIC) == 1);
    msg[0] = 0x00; msg[1] = 0x01; msg[2] = 0x02;
    /* Manufacturer-specific 5-byte header parses. */
    msg[0] = 0x04; msg[1] = 0x11; msg[2] = 0x22; msg[3] = 0x01; msg[4] = 0x02;
    n = 5 + long_rec(msg + 5, ZCL_ATTR_BASIC_DEVICE_CONFIG, 129, 129);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    n = 5 + long_rec(msg + 5, ZCL_ATTR_BASIC_DEVICE_CONFIG, 10, 10);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 1);
    msg[0] = 0x00; msg[1] = 0x01; msg[2] = 0x02;
    /* Unsizable STRUCT, unknown cluster, read-only target, other commands. */
    n = 3 + short_rec(msg + 3, 0x0000, ZCL_DATA_TYPE_STRUCT, 2, 2);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 0);
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 200, 200);
    assert(allows(msg, n, 0x9999) == 1);
    n = 3 + short_rec(msg + 3, 0x0004, ZCL_DATA_TYPE_CHAR_STR, 60, 60);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 1);
    msg[2] = 0x05;
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 129, 129);
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    msg[2] = 0x03;
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 0);
    msg[2] = 0x00;
    assert(allows(msg, n, ZCL_CLUSTER_BASIC) == 1);
    msg[2] = 0x02;
    /* Empty write, trailing byte, truncated header. */
    assert(allows(msg, 3, ZCL_CLUSTER_BASIC) == 1);
    n = 3 + long_rec(msg + 3, ZCL_ATTR_BASIC_DEVICE_CONFIG, 10, 10);
    msg[n] = 0xAA;
    assert(allows(msg, n + 1, ZCL_CLUSTER_BASIC) == 0);
    assert(allows(msg, 4, ZCL_CLUSTER_BASIC) == 0);
    assert(allows(msg, 2, ZCL_CLUSTER_BASIC) == 0);
    /* Zero-length strings fit. */
    n = 3 + short_rec(msg + 3, ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES,
                      ZCL_DATA_TYPE_CHAR_STR, 0, 0);
    assert(allows(msg, n, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT) == 1);
    return 0;
}
'''

CALIBRATION_CODE = r'''
#include <assert.h>
#include <string.h>
#include "zigbee/electrical_measurement_cluster.c"

static unsigned nvm_writes;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    (void)item; (void)size; (void)data; return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    (void)item; (void)size; (void)data; nvm_writes++; return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}

static energy_meter_calibration_t live_cal = {1000, 1000, 1000};
static void fake_get_cal(void *ctx, energy_meter_calibration_t *cal) {
    (void)ctx; *cal = live_cal;
}
static void fake_set_cal(void *ctx, uint32_t v, uint32_t c, uint32_t p) {
    (void)ctx; live_cal.voltage_multiplier = v;
    live_cal.current_multiplier = c; live_cal.power_multiplier = p;
}
static const energy_meter_ops_t fake_ops = {
    .get_calibration = fake_get_cal, .set_calibration = fake_set_cal,
};
static energy_meter_t fake_meter = {&fake_ops, 0};
static electrical_measurement_cluster_t g_elec_meas_cluster;

static struct {
    uint32_t pre;
    electrical_measurement_cluster_t cluster;
    uint32_t post;
} guarded;

static void setup(void) {
    memset(&guarded, 0, sizeof guarded);
    guarded.pre = 0xDEADBEEF;
    guarded.post = 0xCAFEBABE;
    memset(&g_elec_meas_cluster, 0, sizeof g_elec_meas_cluster);
    g_elec_cluster = &g_elec_meas_cluster;
    g_elec_meas_cluster.endpoint = 2;
    g_elec_meas_cluster.meter = &fake_meter;
    live_cal = (energy_meter_calibration_t){1000, 1000, 1000};
    nvm_writes = 0;
}

static void check_canaries(void) {
    assert(guarded.pre == 0xDEADBEEF && guarded.post == 0xCAFEBABE);
}

/* True SDK copy semantics (SDK V3.7.2.0 zcl_setAttrVal): the record's
   declared length is memcpied into the registered destination. */
static void sdk_copy(uint8_t declared, const char *bytes, uint8_t present) {
    uint8_t *dst = (uint8_t *)&guarded.cluster.calibration_values;
    dst[0] = declared;
    memcpy(dst + 1, bytes, declared > present ? present : declared);
    if (declared > present) {
        memset(dst + 1 + present, 'X', declared - present);
    }
    check_canaries();
    /* The trampoline serves the registered cluster instance. */
    memcpy(&g_elec_meas_cluster.calibration_values,
           &guarded.cluster.calibration_values,
           sizeof g_elec_meas_cluster.calibration_values);
}

int main(void) {
    /* Boundary input applies, saves, and keeps canaries intact. */
    setup();
    char valid[36];
    memset(valid, ' ', sizeof valid);
    memcpy(valid, "V1000A2000W3000", sizeof("V1000A2000W3000") - 1);
    sdk_copy(36, valid, 36);
    electrical_measurement_cluster_callback_attr_write_trampoline(2,
        ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES);
    check_canaries();
    assert(live_cal.voltage_multiplier == 1000);
    assert(live_cal.current_multiplier == 2000);
    assert(live_cal.power_multiplier == 3000);
    assert(nvm_writes == 1);
    assert(g_elec_meas_cluster.calibration_values.len <= 36);
    /* Overlong input changes neither meter, NVM, nor retained storage. */
    setup();
    elec_meas_refresh_calibration_values(&g_elec_meas_cluster);
    uint8_t good_len = g_elec_meas_cluster.calibration_values.len;
    char good[36];
    memcpy(good, g_elec_meas_cluster.calibration_values.str, good_len);
    char overlong[40];
    memset(overlong, '9', sizeof overlong);
    memcpy(overlong, "V4294967295A1W1", 15);
    sdk_copy(40, overlong, 40);
    electrical_measurement_cluster_callback_attr_write_trampoline(2,
        ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES);
    check_canaries();
    assert(live_cal.voltage_multiplier == 1000);
    assert(live_cal.current_multiplier == 1000);
    assert(live_cal.power_multiplier == 1000);
    assert(nvm_writes == 0);
    assert(g_elec_meas_cluster.calibration_values.len == good_len);
    assert(memcmp(g_elec_meas_cluster.calibration_values.str, good, good_len) == 0);
    /* A write to another endpoint never touches this cluster. */
    setup();
    sdk_copy(36, valid, 36);
    electrical_measurement_cluster_callback_attr_write_trampoline(3,
        ZCL_ATTR_ELEC_MEAS_CUST_CALIBRATION_VALUES);
    assert(nvm_writes == 0);
    assert(live_cal.voltage_multiplier == 1000);
    return 0;
}
'''


def _build_and_run(tmp_path, name, code):
    binary = tmp_path / name
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DBSEED_PM_B28WRPVX=1',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), str(ROOT / 'src/stub/hal/gpio.c'), '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_write_gate_drops_oversized_and_truncated_strings(tmp_path):
    _build_and_run(tmp_path, 'write-gate-test', GATE_CODE)


def test_overlong_calibration_restores_retained_value(tmp_path):
    _build_and_run(tmp_path, 'calibration-bounds-test', CALIBRATION_CODE)
    gate = (ROOT / 'src/telink/hal/zigbee_zcl.c').read_text()
    assert 'zcl_write_string_gate_allows' in gate
    assert 'ev_buf_free(arg)' in gate
    header = (ROOT / 'src/zigbee/electrical_measurement_cluster.h').read_text()
    assert 'str[255]' in header
    assert 'ELEC_MEAS_CALIBRATION_STR_APP_MAX 36' in header


BLOB_CODE = r'''
#include <assert.h>
#include <string.h>
#include "device_config/config_nv.c"
#include "zigbee/basic_cluster.c"

uint8_t g_multi_press_reset_count;
static uint8_t nvm_config[sizeof(device_config_str_t)];
static unsigned config_item_writes;
static unsigned reboots;
hal_nvm_status_t hal_nvm_read(uint8_t item, uint16_t size, uint8_t *data) {
    if (item == NV_ITEM_DEVICE_CONFIG && size <= sizeof nvm_config) {
        memcpy(data, nvm_config, size);
        return HAL_NVM_SUCCESS;
    }
    return HAL_NVM_NOT_FOUND;
}
hal_nvm_status_t hal_nvm_write(uint8_t item, uint16_t size, uint8_t *data) {
    if (item == NV_ITEM_DEVICE_CONFIG && size <= sizeof nvm_config) {
        memcpy(nvm_config, data, size);
        config_item_writes++;
    }
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_delete(uint8_t item) { (void)item; return HAL_NVM_SUCCESS; }
void hal_zigbee_notify_attribute_changed(uint8_t ep, uint16_t cl, uint16_t at) {
    (void)ep; (void)cl; (void)at;
}
void hal_tasks_init(hal_task_t *t) { (void)t; }
void hal_tasks_schedule(hal_task_t *t, uint32_t ms) { (void)t; (void)ms; }
void hal_tasks_unschedule(hal_task_t *t) { (void)t; }
void schedule_reboot(uint16_t delay_ms) { (void)delay_ms; reboots++; }
network_indicator_t network_indicator;
void network_indicator_from_manual_state(network_indicator_t *indicator) { (void)indicator; }
void device_params_set_multi_press_reset_count(uint8_t value) { (void)value; }

static void seed_last_good(const char *text) {
    size_t len = strlen(text);
    device_config_str_t good;
    memset(&good, 0, sizeof good);
    memcpy(good.data, text, len);
    good.size = (uint16_t)len;
    memcpy(nvm_config, &good, sizeof good);
    memcpy(&device_config_str, &good, sizeof good);
    config_item_writes = 0;
    reboots = 0;
}

/* True SDK copy semantics into the 130-byte destination. Only sizes that fit
   are simulated: larger declared sizes never reach the SDK (gate drops). */
static void sdk_copy(uint16_t declared, const uint8_t *bytes) {
    assert(declared <= sizeof device_config_str.data);
    device_config_str.size = declared;
    memcpy(device_config_str.data, bytes, declared);
}

int main(void) {
    /* Valid-size garbage restores NVM content; config item untouched. */
    seed_last_good("a;b;");
    uint8_t garbage[100];
    memset(garbage, 'Z', sizeof garbage);
    sdk_copy(100, garbage);
    basic_cluster_callback_attr_write_trampoline(ZCL_ATTR_BASIC_DEVICE_CONFIG);
    assert(device_config_str.size == 4);
    assert(memcmp(device_config_str.data, "a;b;", 4) == 0);
    assert(memcmp(nvm_config, &device_config_str, sizeof device_config_str) == 0);
    assert(config_item_writes == 0 && reboots == 0);
    /* Positive control: a valid replacement persists and reboots. */
    seed_last_good("a;b;");
    sdk_copy(4, (const uint8_t *)"a;c;");
    basic_cluster_callback_attr_write_trampoline(ZCL_ATTR_BASIC_DEVICE_CONFIG);
    assert(device_config_str.size == 4);
    assert(memcmp(device_config_str.data, "a;c;", 4) == 0);
    assert(config_item_writes == 1 && reboots == 1);
    return 0;
}
'''


def test_config_blob_restore_keeps_last_good(tmp_path):
    binary = tmp_path / 'config-blob-test'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-DVERSION_STR=1.2.5-bseedr7',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-I', str(ROOT / 'src'), str(ROOT / 'src/stub/hal/gpio.c'), '-x', 'c', '-', '-o', str(binary)],
                   input=BLOB_CODE, text=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
