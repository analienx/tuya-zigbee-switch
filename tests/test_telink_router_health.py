"""Hosted fault tests for Router diagnostics and compile-time power invariants."""
from pathlib import Path
import re
import subprocess
import pytest
from tests.test_telink_counter_persistence import sdk_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('power,rx,passed', [(0,1,True),(1,1,False),(0,0,False),(1,0,False)])
def test_router_power_guard_compiler(power, rx, passed):
    result = subprocess.run(['cc', '-E', '-x', 'c', '-DROUTER=1',
                             f'-DPM_ENABLE={power}', f'-DZB_MAC_RX_ON_WHEN_IDLE={rx}',
                             '-I', str(ROOT/'src/telink/configs'), '-'],
                            input='#include "stack_cfg.h"\n', capture_output=True, text=True)
    assert (result.returncode == 0) is passed, result.stderr


@pytest.mark.parametrize('router', [0, 1])
def test_runtime_snapshot_sdk_widths_irq_timing_and_fault_stop(tmp_path, router):
    sdk = sdk_file('zigbee/common/includes/zb_common.h')
    diag = re.search(r'/\* diagnostics for stack \*/\s*(typedef struct \{.*?\} sys_diagnostics_t;)', sdk, re.S).group(1)
    shim = r'''
#ifndef TEST_TL_COMMON
#define TEST_TL_COMMON
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <setjmp.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef int8_t s8;
#define _attribute_ram_code_sec_
#define S_TIMER_CLOCK_1US 16
#define MAC_CAP_RX_ON_WHEN_IDLE 8
#define POWER_MODE_RECEIVER_SYNCHRONIZED_WHEN_ON_IDLE 0
''' + diag + r'''
static sys_diagnostics_t g_sysDiags;
static struct {u32 usedNum;} g_mPool;
static struct {u8 rxOnWhenIdle;} mac;
static struct {u32 outgoingFrameCounter;} ss_ib;
static u32 ticks, now;
static u8 TL_ZB_NEIGHBOR_TABLE_SIZE=26, TL_ZB_CHILD_TABLE_SIZE=16, ZB_BUF_POOL_SIZE=36;
static u16 ROUTING_TABLE_SIZE=48;
typedef struct {u8 mac_capability_flag;} node_descriptor_t;
typedef struct {u8 current_power_mode;} power_descriptor_t;
static u8 neighbors=7, routes=10, children=3;
static unsigned irq_depth, radio_off, watchdog_stopped;
static jmp_buf fault;
#define MAC_IB() mac
static u32 clock_time(void) {return ticks;}
static u32 drv_disable_irq(void) {irq_depth++;return 1;}
static void drv_restore_irq(u32 r) {(void)r;assert(irq_depth>0);irq_depth--;}
static void rf_set_tx_rx_off(void) {assert(irq_depth);radio_off++;}
static void wd_stop(void) {watchdog_stopped++;longjmp(fault,1);}
static u8 tl_zbNeighborTableNumGet(void) {assert(!irq_depth);return neighbors;}
static u8 tl_zbNeighborTableChildEDNumGet(void) {return children;}
static void af_nodeDescriptorCopy(node_descriptor_t *p) {p->mac_capability_flag=8;}
static void af_powerDescriptorCopy(power_descriptor_t *p) {p->current_power_mode=0;}
#endif
'''
    (tmp_path/'tl_common.h').write_text(shim)
    (tmp_path/'zb_api.h').write_text('#include "tl_common.h"\n')
    (tmp_path/'watchdog.h').write_text('#include "tl_common.h"\n')
    (tmp_path/'telink_size_t_hack.h').write_text('')
    code = '#include "' + (ROOT/'src/telink/hal/router_health.c').as_posix() + '"\n' + r'''
uint32_t hal_millis(void) {return now;}
uint8_t telink_route_entry_count(bool active) {return active ? routes-1 : routes;}
static u32 get32(u8 *p) {return p[0]|((u32)p[1]<<8)|((u32)p[2]<<16)|((u32)p[3]<<24);}
int main(void) {
    mac.rxOnWhenIdle=1;
    ticks=0xfffffff0; hal_telink_stack_service_sample();
    ticks+=16000; hal_telink_stack_service_sample();
    hal_telink_flash_complete(32000); hal_telink_flash_complete(16000);
    hal_telink_counter_retry();
    g_mPool.usedNum=9;
    g_sysDiags.relayedUcast=0xffff;
    g_sysDiags.macTxUcastFail=27;
    g_sysDiags.macRxCrcFail=0xabcdef12;
    g_sysDiags.lastMessageRSSI=-70;
    ss_ib.outgoingFrameCounter=8192;
    hal_telink_nwk_status(0x1234,0x04);
    now=1000; hal_telink_routing_health_update();
    u8 *p=firmware_runtime_snapshot+1;
    assert(firmware_runtime_snapshot[0]==48 && p[0]==1);
    assert(get32(p+4)==1000 && get32(p+8)==2000 && get32(p+12)==2);
    assert(get32(p+20)==1 && get32(p+24)==8192 && (s8)p[41]==-70);
#if ZB_ROUTER_ROLE
    p=firmware_router_snapshot+1;
    assert(p[0]==1 && p[1]==4 && p[2]==7 && p[4]==0x34 && p[5]==0x12);
    assert(p[6]==7 && p[7]==3 && p[8]==26 && p[9]==16);
    assert(p[10]==10 && p[11]==9 && p[12]==48 && p[13]==9 && p[14]==36 && p[15]==9);
    assert(get32(p+20)==1 && p[24]==255 && p[25]==255 && p[30]==27);
    assert(get32(p+44)==0xabcdef12);
    g_sysDiags.relayedUcast=0; g_mPool.usedNum=2; neighbors=1; routes=2;
    now=2000; hal_telink_routing_health_update();
    assert(p[24]==0 && p[25]==0 && p[15]==9); /* raw SDK wrap, retained observed peak */
    assert(firmware_runtime_snapshot[43]==7 && firmware_runtime_snapshot[44]==10);
    nwk_status_count=UINT32_MAX; hal_telink_nwk_status(0xabcd,9);
    assert(nwk_status_count==UINT32_MAX);
#endif
    if(!setjmp(fault)) {hal_telink_counter_fault(6);assert(0);}
    assert(irq_depth==1 && radio_off==1 && watchdog_stopped==1);
    assert(counter_failures==1 && firmware_runtime_snapshot[4]==6);
    return 0;
}
'''
    c=tmp_path/'health.c'; c.write_text(code)
    exe=tmp_path/'health'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-variable', f'-DZB_ROUTER_ROLE={router}',
                    *(['-DROUTER=1'] if router else []),
                    '-I', str(tmp_path), '-I', str(ROOT/'src'),
                    '-I', str(ROOT/'src/telink'), str(c), '-o', str(exe)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(exe)], check=True, capture_output=True, text=True)

