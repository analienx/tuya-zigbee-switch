"""Run shared recovery scheduling with deterministic SDK outcomes on CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_retry_ownership_backoff_and_rollover(tmp_path):
    source = tmp_path / 'recovery.c'
    binary = tmp_path / 'recovery'
    source.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include "hal/bseed_recovery_core.h"

int main(void) {
    const uint8_t ieee[8] = {1,2,3,4,5,6,7,8};
    bseed_recovery_core_t core;
    uint32_t now = UINT32_MAX - 1000u;
    bseed_recovery_init(&core, ieee, now, true);
    assert(bseed_recovery_rung(&core) == BSEED_RECOVERY_SECURE_CURRENT_CHANNEL);
    assert(!bseed_recovery_claim(&core, now, false)); /* OTA/factory-new */
    assert(bseed_recovery_claim(&core, now, true));
    assert(core.pending_since_ms == now);
    assert(!bseed_recovery_claim(&core, now + 900000u, true)); /* SDK owns it */
    assert(core.pending_since_ms == now); /* failed claim cannot mask a wedge */
    bseed_recovery_finish(&core, now, false);
    assert(!core.pending && core.pending_since_ms == 0u);
    assert(bseed_recovery_rung(&core) == BSEED_RECOVERY_SECURE_ALL_CHANNELS);
    const uint32_t expected[] = {30000u, 60000u, 120000u, 300000u, 300000u};
    for (unsigned i = 0; i < 5u; i++) {
        uint32_t delay = bseed_recovery_delay_ms(core.failures, core.stagger_ms);
        assert(delay >= expected[i] && delay <= 300000u);
        assert(core.delay_ms == delay);
        assert(!bseed_recovery_claim(&core, now + delay - 1u, true));
        now += delay; /* wraps naturally */
        assert(bseed_recovery_claim(&core, now, true));
        assert(core.pending_since_ms == now);
        bseed_recovery_finish(&core, now, false);
        assert(bseed_recovery_rung(&core) == (i % 2u == 0u ?
               BSEED_RECOVERY_SECURE_CURRENT_CHANNEL : BSEED_RECOVERY_SECURE_ALL_CHANNELS));
    }
    /* Failure count and elapsed time cannot exhaust the retry schedule. */
    for (unsigned i = 0; i < 300u; i++) {
        bseed_recovery_rung_t before = bseed_recovery_rung(&core);
        now += core.delay_ms;
        assert(bseed_recovery_claim(&core, now, true));
        bseed_recovery_finish(&core, now, false);
        assert(core.delay_ms == 300000u);
        assert(bseed_recovery_rung(&core) != before);
    }
    assert(core.failures == UINT8_MAX);
    now += core.delay_ms;
    assert(bseed_recovery_claim(&core, now, true));
    bseed_recovery_finish(&core, now, true);
    assert(core.failures == 0u && !core.pending);
    assert(core.pending_since_ms == 0u);
    assert(bseed_recovery_rung(&core) == BSEED_RECOVERY_SECURE_CURRENT_CHANNEL);
    bseed_recovery_finish(&core, now, false); /* late duplicate ignored */
    assert(core.failures == 0u);
    return 0;
}
''', encoding='utf8')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(ROOT / 'src'), str(source), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
