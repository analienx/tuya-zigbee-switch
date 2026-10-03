"""Exercise the patched, real pinned SDK counter driver against a NOR-flash fake."""
from pathlib import Path
import re
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from patch_telink_router_runtime import patch_nv, patch_flash


def sdk_file(path):
    fetched = ROOT / 'build/test-sdk' / Path(path).name
    return (fetched if fetched.exists() else ROOT / 'telink_tools/sdk' / path).read_text()


def function(source, name):
    match = re.search(r'(?:static inline bool|nv_sts_t) ' + name + r'\([^;]+?\)\s*\{', source)
    assert match, name
    start = match.start()
    depth = 1
    pos = source.index('{', start) + 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[start:pos]


def test_sdk_runtime_patches_reject_drift_and_second_application():
    nv = sdk_file('proj/drivers/drv_nv.c')
    flash = sdk_file('platform/chip_8258/flash.c')
    with pytest.raises(ValueError):
        patch_nv(nv.replace('return NV_SUCC;\n}\n\nnv_sts_t nv_nwkFrameCountFromFlash',
                            'return ret;\n}\n\nnv_sts_t nv_nwkFrameCountFromFlash'))
    with pytest.raises(ValueError):
        patch_nv(patch_nv(nv))
    with pytest.raises(ValueError):
        patch_flash(flash.replace('flash_wait_done();', 'flash_wait_done(1);'))
    with pytest.raises(ValueError):
        patch_flash(patch_flash(flash))


def test_real_sdk_counter_commit_retry_readback_rollover_and_boot(tmp_path):
    nv = patch_nv(sdk_file('proj/drivers/drv_nv.c'))
    header = sdk_file('proj/drivers/drv_nv.h')
    status = re.search(r'typedef enum \{\s*NV_SUCC,.*?\} nv_sts_t;', header, re.S).group()
    sector = re.search(r'typedef struct \{\s*u16 usedFlag;.*?\} nv_sect_info_t;', header, re.S).group()
    functions = '\n'.join(function(nv, name) for name in (
        'nv_sectInfoCrcCheck', 'nv_sector_read', 'nv_nwkFrameCountSearch',
        'nv_nwkFrameCountSaveToFlashHandler', 'nv_nwkFrameCountSaveToFlash',
        'nv_nwkFrameCountFromFlash'))
    shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef int32_t s32;
#define TRUE true
#define FALSE false
#define NV_MODULE_NWK_FRAME_COUNT 4
#define FLASH_SECTOR_SIZE 4096
#define FLASH_PAGE_SIZE 256
#define MODULE_SECTOR_NUM 2
#define MODULES_START_ADDR(id) (0x10000u + 8192u * (id))
#define NV_SECTOR_SIZE(id) FLASH_SECTOR_SIZE
#define MODULE_SECT_START(id,s) (MODULES_START_ADDR(id)+(s)*FLASH_SECTOR_SIZE)
#define MODULE_SECT_END(id,s) (MODULES_START_ADDR(id)+((s)+1)*FLASH_SECTOR_SIZE)
#define FRAMECOUNT_PAYLOAD_START(s) ((MODULE_SECT_START(4,s)+sizeof(nv_sect_info_t)+3u)&~3u)
#define FRAMECOUNT_NUM_PER_SECT() ((FLASH_SECTOR_SIZE-sizeof(nv_sect_info_t))/4)
#define NV_SECTOR_VALID 0x5A5A
#define NV_SECTOR_VALID_CHECKCRC 0x7A7A
#define NV_SECTOR_VALID_READY_CHECKCRC 0xFAFA
#define NV_SECTOR_INVALID 0x5050
#define NV_SECT_INFO_SECTNO_BITS 2
#define NV_SECT_INFO_SECTNO_BITMASK 3
#define NV_SECT_INFO_CHECK_BITMASK 0x3f
#define SECT_VALID_CHECK(s) ((s).usedFlag==NV_SECTOR_VALID || (s).usedFlag==NV_SECTOR_VALID_CHECKCRC)
#define UPDATE_FRAMECOUNT_THRES 1024u
static u8 memory[0x30000];
static unsigned fail_next, fail_all, lie_write, writes, retries, faults, sends, erased0, erased1;
static jmp_buf stopped;
static void flash_read(u32 a,u32 n,u8 *p) {
    assert(a+n<=sizeof(memory)); memcpy(p,memory+a,n);
}
static void flash_erase(u32 a) {
    assert(a+4096<=sizeof(memory));
    if(a==MODULE_SECT_START(4,0)) erased0++;
    if(a==MODULE_SECT_START(4,1)) erased1++;
    if(!fail_all) memset(memory+a,0xff,4096);
}
static bool flash_writeWithCheck(u32 a,u32 n,u8 *p) {
    assert(a+n<=sizeof(memory)); writes++;
    if(fail_all || fail_next) {if(fail_next) fail_next--;return false;}
    if(lie_write) return true;
    for(u32 i=0;i<n;i++) memory[a+i]&=p[i];
    return !memcmp(memory+a,p,n);
}
static u32 xcrc32(u8 *p,u32 n,u32 crc) {
    for(u32 i=0;i<n;i++) crc=(crc*33u)^p[i];
    return crc;
}
void hal_telink_counter_fault(u8 status) __attribute__((noreturn));
void hal_telink_counter_fault(u8 status) {assert(status!=0); faults++;longjmp(stopped,1);}
void hal_telink_counter_retry(void) {retries++;}
'''
    checks = r'''
static void native_ignores_result(u32 value) {
    nv_nwkFrameCountSaveToFlash(value);
    sends++; /* MUST NOT be reached on an unverified commit */
}
int main(void) {
    u32 loaded=0;
    memset(memory,0xff,sizeof(memory));
    assert(nv_nwkFrameCountSaveToFlash(1024)==NV_SUCC);
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==1024);
    unsigned prior_writes=writes;
    assert(nv_nwkFrameCountSaveToFlash(8192)==NV_SUCC);
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==8192);
    assert(writes>prior_writes);
    prior_writes=writes;
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==8192);
    assert(writes==prior_writes); /* large valid gaps never roll back/rewrite */
    erased0=erased1=0; fail_next=1;
    native_ignores_result(9216);
    assert(sends==1 && retries==1 && erased0==0 && erased1==1);
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==9216);
    fail_all=1;
    if(!setjmp(stopped)) {native_ignores_result(10240); assert(0);}
    assert(faults==1 && sends==1);
    fail_all=0;
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==9216);
    lie_write=1;
    if(!setjmp(stopped)) {native_ignores_result(11264); assert(0);}
    assert(faults==2 && sends==1); /* success status without stored bytes */
    lie_write=0;
    memset(memory,0xff,sizeof(memory)); erased0=erased1=0;
    for(u32 i=0;i<1100;i++) assert(nv_nwkFrameCountSaveToFlash(1024+i)==NV_SUCC);
    assert(erased1>0);
    assert(nv_nwkFrameCountFromFlash(&loaded)==NV_SUCC && loaded==2123);
    if(!setjmp(stopped)) {native_ignores_result(0xfffffbffu); assert(0);}
    assert(faults==3 && sends==1); /* live reservation cannot wrap either */
    u8 sector_number=0;
    assert(nv_nwkFrameCountSaveToFlashHandler(0,&sector_number,0xfffffbffu)==NV_SUCC);
    if(!setjmp(stopped)) {nv_nwkFrameCountFromFlash(&loaded); assert(0);}
    assert(faults==4); /* reserving 1024 would hit erased-word sentinel */
    return 0;
}
'''
    c = tmp_path / 'counter.c'
    c.write_text(shim + status + sector + functions + checks)
    exe = tmp_path / 'counter'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Werror', '-Wno-sign-compare',
                    '-Wno-pointer-to-int-cast', '-Wno-int-to-pointer-cast',
                    str(c), '-o', str(exe)], check=True, capture_output=True, text=True)
    subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=10)

