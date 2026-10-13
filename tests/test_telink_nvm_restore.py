"""Run the application HAL with the pinned SDK's actual record read functions."""
from pathlib import Path
import subprocess

from tests.test_telink_counter_persistence import function, sdk_file

ROOT = Path(__file__).resolve().parents[1]


def test_application_reads_never_delete_or_overflow_energy_records(tmp_path):
    source = sdk_file('proj/drivers/drv_nv.c')
    reads = '\n'.join(function(source, name) for name in (
        'nv_flashReadByIndex', 'nv_flashReadNew'))
    (tmp_path / 'tl_common.h').write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef int32_t s32;
typedef enum { NV_SUCC, NV_INVALID_MODULS, NV_INVALID_ID, NV_ITEM_NOT_FOUND,
    NV_NOT_ENOUGH_SAPCE, NV_ITEM_LEN_NOT_MATCH, NV_CHECK_SUM_ERROR } nv_sts_t;
typedef struct { u16 usedFlag; u8 idName, opSect; } nv_sect_info_t;
typedef struct { u32 offset; u16 size; u8 itemId, usedState; } nv_info_idx_t;
typedef struct { u32 checkSum; u16 size; u8 itemId, used; } itemHdr_t;
typedef struct __attribute__((packed)) { u16 opIndex; u8 opSect; } itemIfno_t;
#define NV_MODULE_APP 6
#define NV_MODULE_NWK_FRAME_COUNT 4
#define MODULE_SECTOR_NUM 2
#define MODULE_IDX_START(id, sect) 0
#define MODULE_IDX_NUM(id) 1
#define MODULE_CONTEXT_START(id, sect, len) 64
#define MODULE_SECT_END(id, sect) 4096
#define ITEM_VALID_FLAG_CHECK(v) ((v) == 0x7a)
#define ITEM_HDR_VALID_CHECK(v) ((v) == 0x5a || (v) == 0x7a)
#define ITEM_HDR_FIELD_VALID_CHECKSUM 0x5a
#define ITEM_HDR_FIELD_VALID_CHECKCRC 0x7a
#define ITEM_FIELD_VALID 0x5a
#define ITEM_FIELD_VALID_SINGLE 0x7a
#define NV_ITEMLEN_MATCH(len, unit) ((len) % (unit) == 0)
static u8 memory[4096];
static bool present;
static unsigned deletes, writes;
static u16 record_size;
static u16 length_limit;
static nv_sts_t size_status = NV_SUCC;
static void flash_read(u32 addr, u32 len, u8 *data) {
    assert(addr + len <= sizeof(memory)); memcpy(data, memory + addr, len);
}
static u16 nv_itemLengthValidCheck(u8 id, u16 len) {
    return length_limit && length_limit < len ? length_limit : len;
}
static u32 xcrc32(u8 *data, u32 len, u32 crc) {
    for (u32 i = 0; i < len; i++) crc = crc * 33u ^ data[i]; return crc;
}
static nv_sts_t nv_sector_read(u8 id, u8 count, nv_sect_info_t *info) {
    info->opSect = 0; return NV_SUCC;
}
static nv_sts_t nv_index_read(u8 id, u8 item, u16 len, u8 sect, u32 count, u16 *idx) {
    *idx = 0; return present && record_size % len == 0 ? NV_SUCC : NV_ITEM_NOT_FOUND;
}
static nv_sts_t nv_itemDeleteByIndex(u8 id, u8 item, u8 sect, u16 idx) {
    deletes++; present = false; return NV_SUCC;
}
static nv_sts_t nv_flashSingleItemSizeGet(u8 id, u8 item, u16 *len) {
    *len = record_size; return size_status != NV_SUCC ? size_status :
        (present ? NV_SUCC : NV_ITEM_NOT_FOUND);
}
static nv_sts_t nv_flashWriteNew(u8 single, u16 id, u8 item, u16 len, u8 *data) {
    writes++; return NV_SUCC;
}
static nv_sts_t nv_flashSingleItemRemove(u8 id, u8 item, u16 len) { return NV_SUCC; }
static nv_sts_t nv_resetModule(u8 id) { return NV_SUCC; }
''')
    code = r'''
#include <assert.h>
#include "tl_common.h"
'''
    # The fake common header is included once, before HAL inclusion below.
    header = tmp_path / 'tl_common.h'
    header.write_text('#pragma once\n' + header.read_text())
    code += reads + '\n#include "telink/hal/nvm.c"\n' + r'''
static void seed(u16 size, bool corrupt) {
    memset(memory, 0, sizeof(memory)); present = true; record_size = size;
    nv_info_idx_t idx = {.offset = 64, .size = 8 + size, .itemId = 64, .usedState = 0x7a};
    itemHdr_t hdr = {.size = size, .itemId = 64, .used = 0x5a, .checkSum = 128 + corrupt};
    memcpy(memory, &idx, sizeof(idx)); memcpy(memory + 64, &hdr, sizeof(hdr));
    memory[72] = 128;
}
static void seed_crc(uint64_t total) {
    seed(8, false);
    nv_info_idx_t *idx = (nv_info_idx_t *)memory;
    itemHdr_t *hdr = (itemHdr_t *)(memory + 64);
    hdr->used = ITEM_HDR_FIELD_VALID_CHECKCRC;
    memcpy(memory + 72, &total, 8);
    u32 payload = xcrc32(memory + 72, 8, 0xffffffff);
    payload = xcrc32((u8 *)&hdr->size, 4, payload);
    u32 index = xcrc32((u8 *)idx, sizeof(*idx) - 1, 0xffffffff);
    hdr->checkSum = (index << 16) | (payload & 0xffff);
}
int main(void) {
    struct { uint64_t total, guard; } value = {.guard = 0x123456789abcdef0ULL};
    seed(8, false);
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_SUCCESS);
    assert(value.total == 128 && value.guard == 0x123456789abcdef0ULL);
    seed(8, true);
    for (int retry = 0; retry < 4; retry++)
        assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    assert(present && deletes == 0 && writes == 0);
    memory[64]--; /* recover a transient checksum fault without losing the record */
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_SUCCESS);
    assert(value.total == 128);
    seed_crc(0x123456789aULL);
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_SUCCESS);
    assert(value.total == 0x123456789aULL);
    length_limit = 4; value.total = 0;
    /* Real SDK reports success but silently copies only the registered prefix. */
    assert(nv_flashReadByIndex(NV_MODULE_APP, 64, 0, 0, 8, (u8 *)&value.total) == NV_SUCC);
    assert(value.total == 0x3456789aULL);
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_SUCCESS);
    assert(value.total == 0x123456789aULL);
    length_limit = 0;
    ((itemHdr_t *)(memory + 64))->checkSum ^= 0x10000; /* index checksum */
    assert(nv_flashReadByIndex(NV_MODULE_APP, 64, 0, 0, 8, (u8 *)&value.total) == NV_SUCC);
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    seed_crc(128); memory[72] ^= 1; /* payload checksum */
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    assert(present && deletes == 0 && writes == 0);
    seed(16, false); /* SDK accepts multiples, but HAL must reject before copying */
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    assert(value.guard == 0x123456789abcdef0ULL);
    seed(3, false); /* wrong size must stay an error, never become NOT_FOUND */
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    seed(8, false);
    ((nv_info_idx_t *)memory)->itemId = 63;
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    seed(8, false);
    ((itemHdr_t *)(memory + 64))->used = 0xff;
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    seed(8, false);
    ((itemHdr_t *)(memory + 64))->size = 16; /* header/index disagreement */
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    assert(value.guard == 0x123456789abcdef0ULL);
    seed(8, false);
    ((nv_info_idx_t *)memory)->offset = 4092;
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    size_status = NV_CHECK_SUM_ERROR;
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_ERROR);
    size_status = NV_SUCC; present = false;
    assert(hal_nvm_read(64, 8, (u8 *)&value.total) == HAL_NVM_NOT_FOUND);
    assert(deletes == 0 && writes == 0);
    assert(hal_nvm_read(64, 0, (u8 *)&value.total) == HAL_NVM_ERROR);
    return 0;
}
'''
    binary = tmp_path / 'nvm-read'
    subprocess.run(['cc', '-std=c99', '-DHAL_STUB', '-I', str(tmp_path),
                    '-I', str(ROOT / 'src'), '-x', 'c', '-', '-o', str(binary)],
                   input=code, text=True, capture_output=True, check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
