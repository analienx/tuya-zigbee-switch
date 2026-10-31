#include "router_recovery_budget.h"
#include "device_config/nvm_items.h"
#include "hal/nvm.h"
#include <stdint.h>

/* Tiny, append/rewrite-once-per-rescue record; max two automatic software
 * rescue boots until 12h and 24 consecutive coordinator acknowledgements.
 * Never use reset reason, host clock or user power cycling to waive the cap.
 * NVM corruption/read/write/verification failure denies automatic reboot. */
#define BSEED_RESCUE_MAGIC           0xB5u
#define BSEED_RESCUE_MAX             2u
#define BSEED_RESCUE_GOOD_MS         (12u * 60u * 60u * 1000u)
#define BSEED_RESCUE_GOOD_REPLIES    24u
typedef struct {
    uint8_t magic;
    uint8_t used;
    uint8_t used_inverse;
    uint8_t crc;
} bseed_rescue_record_t;

static bool     budget_loaded;
static uint8_t  used_reboots;
static bool     good_run_started;
static uint32_t good_run_since;
static uint8_t  good_replies;

static uint8_t budget_crc(const bseed_rescue_record_t *r) {
    return (uint8_t)(r->magic ^ r->used ^ r->used_inverse ^ 0x5au);
}

static void budget_load(void) {
    if (budget_loaded)
        return;

    budget_loaded = true;
    bseed_rescue_record_t r  = { 0 };
    hal_nvm_status_t      st = hal_nvm_read(NV_ITEM_ROUTER_RESCUE_BUDGET,
                                            sizeof(r), (uint8_t *)&r);
    if (st == HAL_NVM_NOT_FOUND) {
        used_reboots = 0;
    } else if (st == HAL_NVM_SUCCESS &&
               r.magic == BSEED_RESCUE_MAGIC &&
               r.used <= BSEED_RESCUE_MAX &&
               r.used_inverse == (uint8_t)(r.used ^ 0xffu) &&
               r.crc == budget_crc(&r)) {
        used_reboots = r.used;
    } else {
        used_reboots = BSEED_RESCUE_MAX;
    }
}

static bool budget_store(uint8_t used) {
    bseed_rescue_record_t r = {
        BSEED_RESCUE_MAGIC, used, (uint8_t)(used ^ 0xffu), 0
    };

    r.crc = budget_crc(&r);
    if (hal_nvm_write(NV_ITEM_ROUTER_RESCUE_BUDGET,
                      sizeof(r), (uint8_t *)&r) != HAL_NVM_SUCCESS)
        return false;

    bseed_rescue_record_t verify = { 0 };
    if (hal_nvm_read(NV_ITEM_ROUTER_RESCUE_BUDGET,
                     sizeof(verify), (uint8_t *)&verify) != HAL_NVM_SUCCESS)
        return false;

    return verify.magic == r.magic && verify.used == r.used &&
           verify.used_inverse == r.used_inverse && verify.crc == r.crc;
}

bool bseed_router_rescue_budget_consume(void) {
    budget_load();
    if (used_reboots >= BSEED_RESCUE_MAX)
        return false;

    if (!budget_store((uint8_t)(used_reboots + 1u))) {
        used_reboots = BSEED_RESCUE_MAX;
        return false;
    }
    used_reboots++;
    good_run_started = false;
    good_replies     = 0;
    return true;
}

void bseed_router_rescue_budget_note_failure(void) {
    good_run_started = false;
    good_replies     = 0;
}

void bseed_router_rescue_budget_note_good_coordinator(uint32_t now_ms) {
    budget_load();
    if (used_reboots == 0)
        return;

    if (!good_run_started) {
        good_run_started = true;
        good_run_since   = now_ms;
        good_replies     = 0;
    }
    if (good_replies < BSEED_RESCUE_GOOD_REPLIES)
        good_replies++;
    if (good_replies >= BSEED_RESCUE_GOOD_REPLIES &&
        (uint32_t)(now_ms - good_run_since) >= BSEED_RESCUE_GOOD_MS) {
        if (budget_store(0))
            used_reboots = 0;
        else
            used_reboots = BSEED_RESCUE_MAX;
        good_run_started = false;
        good_replies     = 0;
    }
}
