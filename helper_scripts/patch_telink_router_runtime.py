"""Fail-closed, build-local patches for the pinned Telink counter/flash paths."""
import argparse
from pathlib import Path


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('unsupported SDK source; Router runtime hook not applied')
    return source.replace(old, new, 1)


def patch_nv(source):
    start = 'nv_sts_t nv_nwkFrameCountSaveToFlash(u32 frameCount)\n{'
    end = '\nnv_sts_t nv_nwkFrameCountFromFlash(u32 *frameCount)\n{'
    old_save = source[source.index(start):source.index(end)].rstrip()
    expected = """nv_sts_t nv_nwkFrameCountSaveToFlash(u32 frameCount)
{
    u8 sect = 0xff;
    nv_sts_t ret = nv_nwkFrameCountSaveToFlashHandler(0, &sect, frameCount);
    if (ret == NV_CHECK_SUM_ERROR) {
        nv_nwkFrameCountSaveToFlashHandler(1, &sect, frameCount);
    }
    return NV_SUCC;
}"""
    if old_save != expected:
        raise ValueError('unsupported SDK counter-save source')
    new_save = """extern void hal_telink_counter_fault(u8 status) __attribute__((noreturn));
extern void hal_telink_counter_retry(void);

nv_sts_t nv_nwkFrameCountSaveToFlash(u32 frameCount)
{
    /* The native callers ignore return status. Verify the durable record
     * before returning, including an empty sector's first successful write.
     * Recover into the other sector rather than erasing the valid one. */
    nv_sect_info_t before;
    if (frameCount >= 0xffffffffu - UPDATE_FRAMECOUNT_THRES) {
        hal_telink_counter_fault(NV_CHECK_SUM_ERROR);
    }
    u8 sect = 0xff;
    if (nv_sector_read(NV_MODULE_NWK_FRAME_COUNT, MODULE_SECTOR_NUM, &before) == NV_SUCC) {
        sect = before.opSect;
    }
    nv_sts_t ret = nv_nwkFrameCountSaveToFlashHandler(0, &sect, frameCount);
    if (ret == NV_CHECK_SUM_ERROR) {
        hal_telink_counter_retry();
        ret = nv_nwkFrameCountSaveToFlashHandler(1, &sect, frameCount);
    }
    nv_sect_info_t after;
    u32 stored = 0, next = 0;
    nv_sts_t verified = nv_sector_read(NV_MODULE_NWK_FRAME_COUNT, MODULE_SECTOR_NUM, &after);
    if (verified == NV_SUCC) {
        verified = nv_nwkFrameCountSearch(NV_MODULE_NWK_FRAME_COUNT, after.opSect, &stored, &next);
    }
    if (verified != NV_SUCC || stored != frameCount) {
        hal_telink_counter_fault(ret != NV_SUCC ? ret : NV_CHECK_SUM_ERROR);
    }
    return NV_SUCC;
}"""
    source = replace_once(source, expected, new_save)
    old_load = source[source.index(end) + 1:source.index('\n}', source.index(end)) + 2]
    if 'pCnt[0] + UPDATE_FRAMECOUNT_THRES' not in old_load:
        raise ValueError('unsupported SDK counter-load source')
    new_load = """nv_sts_t nv_nwkFrameCountFromFlash(u32 *frameCount)
{
    nv_sect_info_t sector;
    u32 next = 0;
    nv_sts_t ret = nv_sector_read(NV_MODULE_NWK_FRAME_COUNT, MODULE_SECTOR_NUM, &sector);
    if (ret == NV_SUCC) {
        ret = nv_nwkFrameCountSearch(NV_MODULE_NWK_FRAME_COUNT, sector.opSect, frameCount, &next);
        /* Never lower a valid stored counter because two records are far
         * apart. A boot reserves another 1024; refuse its unsigned wrap. */
        if (ret == NV_SUCC && *frameCount >= 0xffffffffu - UPDATE_FRAMECOUNT_THRES) {
            hal_telink_counter_fault(NV_CHECK_SUM_ERROR);
        }
    }
    return ret;
}"""
    return replace_once(source, old_load, new_load)


def patch_flash(source):
    if 'hal_telink_flash_complete' in source:
        raise ValueError('SDK flash hook already applied')
    anchor = '_attribute_ram_code_sec_noinline_ unsigned char flash_mspi_write_ram('
    if source.count(anchor) != 1:
        raise ValueError('unsupported SDK flash source')
    before, tail = source.split(anchor, 1)
    tail = replace_once(tail, 'unsigned char r = irq_disable();',
                        'unsigned char r = irq_disable();\n\tunsigned int started = clock_time();')
    tail = replace_once(tail, 'flash_wait_done();',
                        'flash_wait_done();\n\thal_telink_flash_complete(clock_time() - started);')
    return before + 'extern void hal_telink_flash_complete(unsigned int elapsed_ticks);\n' + anchor + tail


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--kind', choices=('nv', 'flash'), required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text((patch_nv if a.kind == 'nv' else patch_flash)(
        a.source.read_text()), encoding='utf-8')

