"""Run the actual pinned SDK timer with the production HAL on hosted CI."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper_scripts"))
from patch_telink_timing import patch


def test_sdk_clock_wrap_sleep_and_tasks_with_full_pool(tmp_path):
    sdk = ROOT / "build/test-sdk"
    if not (sdk / "ev_timer.c").exists():
        sdk = ROOT / "telink_tools/sdk/proj/os"
    source = sdk / "ev_timer.c"
    assert source.exists(), "CI must fetch the pinned SDK timer"
    proj = tmp_path / "proj"
    os_dir = proj / "os"
    os_dir.mkdir(parents=True)
    (os_dir / "ev_timer.c").write_text(patch(source.read_text()))
    (os_dir / "ev_timer.h").write_text((sdk / "ev_timer.h").read_text())
    (proj / "tl_common.h").write_text(r'''
#ifndef FAKE_TL_COMMON_H
#define FAKE_TL_COMMON_H
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef int32_t s32;
#define TRUE true
#define FALSE false
#define S_TIMER_CLOCK_1US 16u
#define SUCCESS 0
#define NO_TIMER_AVAIL 1
#define TIMER_CANCEL_NOT_ALLOWED 2
#define ZB_EXCEPTION_POST(code) ((void)0)
static u32 ticks;
static u32 clock_time(void) {return ticks;}
static u32 drv_disable_irq(void) {return 0;}
static void drv_restore_irq(u32 r) {(void)r;}
static void ev_rtc_update(u32 ms) {(void)ms;}
#pragma pack(push, 1)
#include "os/ev_timer.h"
#pragma pack(pop)
#define LIST_EXIST(head,item,out) do {out=head; while(out && out!=item) out=out->next;} while(0)
#define LIST_ADD(head,item) do {item->next=head; head=item;} while(0)
#define LIST_DELETE(head,item) do {ev_timer_event_t **p=&head; while(*p && *p!=item) p=&(*p)->next; if(*p) *p=item->next;} while(0)
#endif
''')
    harness = tmp_path / "test.c"
    harness.write_text(r'''
#include <assert.h>
#include "os/ev_timer.c"
#include "telink/hal/timer.c"
#include "telink/hal/tasks.c"
static hal_task_t task, other;
static int fired, other_fired;
static int pooled(void *arg) {(void)arg; return 0;}
static void other_handler(void *arg) {(void)arg; other_fired++;}
static void handler(void *arg) {
    (void)arg;
    fired++;
    if (fired < 3) hal_tasks_schedule(&task, 0);
    else {hal_tasks_schedule(&task, 5); hal_tasks_unschedule(&task);}
}
static void step(u32 ms) {ticks += ms * 16000u; ev_timer_process();}
int main(void) {
    ev_timer_init();
    for(int i=0;i<TIMER_EVENT_NUM;i++) assert(ev_timer_taskPost(pooled,0,60000));
    assert(!ev_timer_taskPost(pooled,0,10));
    task.handler=handler; other.handler=other_handler;
    hal_tasks_init(&task); hal_tasks_init(&other);
    hal_tasks_schedule(&task,0);
    step(1); assert(fired==1);
    step(1); assert(fired==2);
    step(1); assert(fired==3);
    step(10); assert(fired==3 && ev_timer.timerEventPool.used_num==TIMER_EVENT_NUM);
    hal_tasks_schedule(&other,10);
    step(5);
    hal_tasks_schedule(&other,10);
    step(5); assert(!other_fired);
    step(5); assert(other_fired==1);
    hal_tasks_schedule(&other,1); hal_tasks_unschedule(&other);
    step(2); assert(other_fired==1);
    u32 before=hal_millis();
    for(int i=0;i<600;i++) {ticks+=8000; ev_timer_process();}
    assert(hal_millis()==before+300); /* fractional milliseconds retained */
    before=hal_millis();
    for(int i=0;i<600;i++) step(1000);
    assert(hal_millis()==before+600000); /* multiple hardware wraps, >5 minutes */
    before=hal_millis();
    ev_timer_update(30000); /* same path used by retention sleep */
    assert(hal_millis()==before+30000);
    before=hal_millis();
    hal_telink_time_update(UINT32_MAX);
    hal_telink_time_update(2);
    assert(hal_millis()==before+1); /* public uint32 millisecond wrap */
    return 0;
}
''')
    binary = tmp_path / "timer-test"
    subprocess.run(["cc", "-std=c99", "-fno-strict-aliasing", "-fno-pie",
                    "-no-pie", "-DHAL_TELINK", "-I", str(proj), "-I",
                    str(ROOT / "src"), str(harness), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, timeout=10)


def test_clock_hook_rejects_unreviewed_sdk():
    import pytest
    with pytest.raises(ValueError):
        patch('#include "ev_timer.h"\n')
