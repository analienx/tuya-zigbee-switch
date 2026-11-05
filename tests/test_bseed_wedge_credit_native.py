"""Exercise persistent wedge credit with simulated flash and power loss on CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_wedge_credit_persists_and_fails_closed(tmp_path):
    source = tmp_path / "wedge.c"
    binary = tmp_path / "wedge"
    source.write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "hal/nvm.h"
#include "device_config/nvm_items.h"
static uint8_t record[4];
static bool present, bad_read, bad_write;
static unsigned writes;
hal_nvm_status_t hal_nvm_read(uint8_t id, uint16_t size, uint8_t *data) {
    assert(id == NV_ITEM_RECOVERY_WEDGE_CREDIT && size == 4);
    if (bad_read) return HAL_NVM_ERROR;
    if (!present) return HAL_NVM_NOT_FOUND;
    memcpy(data, record, 4);
    return HAL_NVM_SUCCESS;
}
hal_nvm_status_t hal_nvm_write(uint8_t id, uint16_t size, uint8_t *data) {
    assert(id == NV_ITEM_RECOVERY_WEDGE_CREDIT && size == 4);
    writes++;
    if (bad_write) return HAL_NVM_ERROR;
    memcpy(record, data, 4);
    present = true;
    return HAL_NVM_SUCCESS;
}
#include "telink/hal/recovery_wedge_credit.c"
static void restart(void) {
    credit_loaded = credit_usable = wedge_active = false;
    credit_chunks = 0;
    wedge_checkpoint_ms = 0;
}
int main(void) {
    const uint32_t six = 6u * 60u * 60u * 1000u;
    uint32_t now = UINT32_MAX - 1000u;
    assert(!bseed_wedge_credit_tick(now, false));
    assert(!bseed_wedge_credit_consume());
    assert(!bseed_wedge_credit_tick(now, true));
    assert(!bseed_wedge_credit_tick(now + six - 1u, true));
    assert(writes == 0);
    assert(!bseed_wedge_credit_tick(now + six, true)); /* clock wrap */
    assert(writes == 1);
    /* Coordinated counter/inverse corruption must not preserve the CRC. */
    record[1] = 4u; record[2] = (uint8_t)(4u ^ 0xffu);
    restart();
    assert(!bseed_wedge_credit_tick(0u, true));
    assert(!bseed_wedge_credit_tick(4u * six, true));
    assert(!bseed_wedge_credit_consume());
    record[1] = 1u; record[2] = (uint8_t)(1u ^ 0xffu);
    restart(); /* checkpoint survives; partial time does not */
    now = 123u;
    assert(!bseed_wedge_credit_tick(now, true));
    assert(!bseed_wedge_credit_tick(now + six, true));
    assert(!bseed_wedge_credit_tick(now + 2u * six, true));
    assert(bseed_wedge_credit_tick(now + 3u * six, true));
    assert(writes == 4);
    assert(bseed_wedge_credit_consume());
    assert(writes == 5 && !bseed_wedge_credit_consume());
    restart();
    assert(!bseed_wedge_credit_tick(0u, true));
    assert(!bseed_wedge_credit_tick(six - 1u, true));
    restart();
    record[3] ^= 1u;
    assert(!bseed_wedge_credit_tick(0u, true));
    assert(!bseed_wedge_credit_tick(4u * six, true));
    assert(!bseed_wedge_credit_consume());
    present = false; restart(); bad_read = true;
    assert(!bseed_wedge_credit_tick(0u, true));
    assert(!bseed_wedge_credit_tick(4u * six, true));
    bad_read = false; restart(); bad_write = true;
    assert(!bseed_wedge_credit_tick(0u, true));
    assert(!bseed_wedge_credit_tick(six, true));
    assert(!bseed_wedge_credit_consume());
    return 0;
}
''', encoding="utf8")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I",
                    str(ROOT / "src"), str(source), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
