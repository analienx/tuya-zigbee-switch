"""Connect the HAL clock to the pinned SDK's wrap/sleep-aware elapsed time."""
import argparse
from pathlib import Path


def patch(source: str) -> str:
    anchors = ('#include "ev_timer.h"', '    ev_rtc_update(updateTime);',
               '            s32 t = timerEvt->cb(timerEvt->data);',
               '            timerEvt->isBusy = 0;')
    if any(source.count(anchor) != 1 for anchor in anchors):
        raise ValueError("unsupported SDK timer source; elapsed-time hook not applied")
    source = source.replace(anchors[0], anchors[0] +
                            '\nextern void hal_telink_time_update(u32 elapsed_ms);'
                            '\nextern int hal_telink_task_finish(ev_timer_event_t *evt, int result);')
    source = source.replace(anchors[1], anchors[1] +
                            '\n    hal_telink_time_update(updateTime);')
    # Keep handlers interruptible, but finalize a self/ISR rearm atomically.
    source = source.replace(anchors[2], anchors[2] +
                            '\n            u32 callbackIrq = drv_disable_irq();'
                            '\n            t = hal_telink_task_finish(timerEvt, t);')
    return source.replace(anchors[3], anchors[3] +
                          '\n            drv_restore_irq(callbackIrq);')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(patch(args.source.read_text()), encoding="utf-8")
