#include "recovery_diagnostics.h"
#include "device_config/nvm_items.h"
#include "hal/nvm.h"

#define BSEED_RECOVERY_DIAG_MAGIC    0xD9u

typedef struct {
    uint8_t magic;
    uint8_t reason;
    uint8_t sdk_state;
    uint8_t crc;
} bseed_diag_record_t;

static bool    diag_loaded, diag_storage_ok, diag_present;
static uint8_t diag_reason, diag_sdk_state;

static uint8_t diag_crc(const bseed_diag_record_t *r) {
    const uint8_t bytes[] = { r->magic, r->reason, r->sdk_state };
    uint8_t       crc     = 0u;

    for (unsigned i = 0; i < sizeof(bytes); i++) {
        crc ^= bytes[i];
        for (unsigned b = 0; b < 8u; b++) {
            crc = (uint8_t)((crc & 0x80u) ?
                            (((uint32_t)crc << 1) ^ 0x07u) :
                            ((uint32_t)crc << 1));
        }
    }
    return crc;
}

static void diag_load(void) {
    if (diag_loaded)
        return;

    diag_loaded = true;
    bseed_diag_record_t record = { 0 };
    hal_nvm_status_t    status = hal_nvm_read(NV_ITEM_RECOVERY_DIAGNOSTIC,
                                              sizeof(record), (uint8_t *)&record);
    if (status == HAL_NVM_NOT_FOUND) {
        diag_storage_ok = true;
        return;
    }
    if (status == HAL_NVM_SUCCESS &&
        record.magic == BSEED_RECOVERY_DIAG_MAGIC &&
        record.reason <= BSEED_RESCUE_REASON_CLIENT_QUEUE_STALL &&
        record.crc == diag_crc(&record)) {
        diag_storage_ok = true;
        diag_present    = true;
        diag_reason     = record.reason;
        diag_sdk_state  = record.sdk_state;
    }
}

bool bseed_recovery_diag_record(bseed_rescue_reason_t reason, uint8_t sdk_state) {
    diag_load();
    if (!diag_storage_ok || reason == BSEED_RESCUE_REASON_NONE ||
        reason > BSEED_RESCUE_REASON_CLIENT_QUEUE_STALL)
        return false;

    if (diag_present && diag_reason == (uint8_t)reason &&
        diag_sdk_state == sdk_state)
        return true;

    bseed_diag_record_t record = {
        BSEED_RECOVERY_DIAG_MAGIC, (uint8_t)reason, sdk_state, 0u
    };
    record.crc = diag_crc(&record);
    if (hal_nvm_write(NV_ITEM_RECOVERY_DIAGNOSTIC, sizeof(record),
                      (uint8_t *)&record) != HAL_NVM_SUCCESS) {
        diag_storage_ok = false;
        return false;
    }
    bseed_diag_record_t verify = { 0 };
    if (hal_nvm_read(NV_ITEM_RECOVERY_DIAGNOSTIC, sizeof(verify),
                     (uint8_t *)&verify) != HAL_NVM_SUCCESS ||
        verify.magic != record.magic || verify.reason != record.reason ||
        verify.sdk_state != record.sdk_state || verify.crc != record.crc) {
        diag_storage_ok = false;
        return false;
    }
    diag_present   = true;
    diag_reason    = (uint8_t)reason;
    diag_sdk_state = sdk_state;
    return true;
}

bool bseed_recovery_diag_last(uint8_t *reason, uint8_t *sdk_state) {
    diag_load();
    if (reason != 0)
        *reason = diag_present ? diag_reason : 0u;
    if (sdk_state != 0)
        *sdk_state = diag_present ? diag_sdk_state : 0u;
    return diag_storage_ok && diag_present;
}
