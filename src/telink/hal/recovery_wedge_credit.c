#include "recovery_wedge_credit.h"
#include "device_config/nvm_items.h"
#include "hal/nvm.h"

#define WEDGE_CREDIT_MAGIC       0xD7u
#define WEDGE_CREDIT_MAX         4u
#define WEDGE_CREDIT_CHUNK_MS    (6u * 60u * 60u * 1000u)

typedef struct {
    uint8_t magic;
    uint8_t chunks;
    uint8_t inverse;
    uint8_t checksum;
} wedge_credit_record_t;

static bool     credit_loaded, credit_usable, wedge_active;
static uint8_t  credit_chunks;
static uint32_t wedge_checkpoint_ms;

static uint8_t credit_checksum(const wedge_credit_record_t *r) {
    /* CRC-8/ATM over the actual stored bytes, including both counter fields. */
    uint8_t       crc     = 0u;
    const uint8_t bytes[] = { r->magic, r->chunks, r->inverse };

    for (unsigned i = 0; i < sizeof(bytes); i++) {
        crc ^= bytes[i];
        for (unsigned bit = 0; bit < 8u; bit++)
            crc = (uint8_t)((crc & 0x80u) ? ((uint32_t)crc << 1) ^ 0x07u : (uint32_t)crc << 1);
    }
    return crc;
}

static void credit_load(void) {
    if (credit_loaded)
        return;

    credit_loaded = true;
    wedge_credit_record_t r      = { 0 };
    hal_nvm_status_t      status = hal_nvm_read(NV_ITEM_RECOVERY_WEDGE_CREDIT,
                                                sizeof(r), (uint8_t *)&r);
    if (status == HAL_NVM_NOT_FOUND) {
        credit_chunks = 0u;
        credit_usable = true;
    } else if (status == HAL_NVM_SUCCESS && r.magic == WEDGE_CREDIT_MAGIC &&
               r.chunks <= WEDGE_CREDIT_MAX &&
               r.inverse == (uint8_t)(r.chunks ^ 0xffu) &&
               r.checksum == credit_checksum(&r)) {
        credit_chunks = r.chunks;
        credit_usable = true;
    }
}

static bool credit_store(uint8_t chunks) {
    wedge_credit_record_t r = {
        WEDGE_CREDIT_MAGIC, chunks, (uint8_t)(chunks ^ 0xffu), 0u
    };

    r.checksum = credit_checksum(&r);
    if (hal_nvm_write(NV_ITEM_RECOVERY_WEDGE_CREDIT, sizeof(r),
                      (uint8_t *)&r) != HAL_NVM_SUCCESS)
        return false;

    wedge_credit_record_t verify = { 0 };
    if (hal_nvm_read(NV_ITEM_RECOVERY_WEDGE_CREDIT, sizeof(verify),
                     (uint8_t *)&verify) != HAL_NVM_SUCCESS)
        return false;

    return verify.magic == r.magic && verify.chunks == r.chunks &&
           verify.inverse == r.inverse && verify.checksum == r.checksum;
}

bool bseed_wedge_credit_tick(uint32_t now_ms, bool wedged) {
    credit_load();
    if (!credit_usable)
        return false;

    if (!wedged) {
        wedge_active = false;
        return false;
    }
    if (!wedge_active) {
        wedge_active        = true;
        wedge_checkpoint_ms = now_ms;
    }
    uint32_t elapsed = (uint32_t)(now_ms - wedge_checkpoint_ms);
    if (credit_chunks < WEDGE_CREDIT_MAX && elapsed >= WEDGE_CREDIT_CHUNK_MS) {
        uint32_t new_chunks = credit_chunks + elapsed / WEDGE_CREDIT_CHUNK_MS;
        if (new_chunks > WEDGE_CREDIT_MAX)
            new_chunks = WEDGE_CREDIT_MAX;
        if (!credit_store((uint8_t)new_chunks)) {
            credit_usable = false;
            return false;
        }
        wedge_checkpoint_ms += (new_chunks - credit_chunks) * WEDGE_CREDIT_CHUNK_MS;
        credit_chunks        = (uint8_t)new_chunks;
    }
    return credit_chunks == WEDGE_CREDIT_MAX;
}

bool bseed_wedge_credit_consume(void) {
    credit_load();
    if (!credit_usable || !wedge_active || credit_chunks != WEDGE_CREDIT_MAX)
        return false;

    if (!credit_store(0u)) {
        credit_usable = false;
        return false;
    }
    credit_chunks = 0u;
    wedge_active  = false;
    return true;
}
