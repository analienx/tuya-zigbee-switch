"""Run the actual side-effect-free C Router recovery policy on GitHub Actions.

Unlike source-string tests, this compiles the same functions called by the
Telink firmware and exercises thresholds, timing, callback ownership and wrap.
Never run this on a user's workstation: repository tests are GitHub-hosted.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "src/hal/router_recovery_policy.h"
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"

PROGRAM = r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "src/hal/router_recovery_policy.h"

#define CHECK(test) do { if (!(test)) {     fprintf(stderr, "Router policy failed at line %d: %s\n", __LINE__, #test);     return 1; } } while (0)

int main(void) {
    const uint32_t min_20m = 20u * 60u * 1000u;
    const uint32_t min_60m = 60u * 60u * 1000u;
    const uint32_t min_90m = 90u * 60u * 1000u;
    const uint32_t min_3h = 3u * 60u * 60u * 1000u;
    const uint32_t now = 30000000u;

    CHECK(!bseed_router_timeout_evidence(3u, 4u, now,
                                        now - min_20m - 1u, min_20m));
    CHECK(!bseed_router_timeout_evidence(4u, 4u, now,
                                        now - min_20m + 1u, min_20m));
    CHECK(bseed_router_timeout_evidence(4u, 4u, now,
                                       now - min_20m, min_20m));
    /* Route repair first: no rejoin under 4 confirmed failures, no instant
     * scan during neighbor activity, a full 15-minute fleet-wide stagger. */
    CHECK(!bseed_router_rejoin_eligible(false, false, now,
          now - min_60m, now - 1000u, min_20m, min_60m, min_20m,
          15u * 60u * 1000u));
    CHECK(!bseed_router_rejoin_eligible(true, true, now,
          now - min_60m - 1u, now - 1000u, min_20m, min_60m, min_20m, 0u));
    CHECK(!bseed_router_rejoin_eligible(true, false, now,
          now - min_60m - 1u, now - 1000u, min_20m, min_60m, min_20m,
          15u * 60u * 1000u));
    CHECK(bseed_router_rejoin_eligible(true, false, now,
          now - min_60m - 15u * 60u * 1000u, now - 1000u,
          min_20m, min_60m, min_20m, 15u * 60u * 1000u));
    CHECK(!bseed_router_rejoin_eligible(true, false, now,
          now - min_20m, now - 30u * 60u * 1000u,
          min_20m, min_60m, min_20m, 15u * 60u * 1000u));
    CHECK(bseed_router_rejoin_eligible(true, false, now,
          now - min_20m - 15u * 60u * 1000u,
          now - 30u * 60u * 1000u, min_20m, min_60m,
          min_20m, 15u * 60u * 1000u));
    CHECK(bseed_router_rejoin_eligible(true, false, 1000u,
          (uint32_t)(1000u - min_20m - 15u * 60u * 1000u),
          (uint32_t)(1000u - min_60m), min_20m, min_60m,
          min_20m, 15u * 60u * 1000u));

    CHECK(!bseed_router_queue_stall_evidence(7u, 8u, now,
                                            now - min_60m, min_60m));
    CHECK(!bseed_router_queue_stall_evidence(8u, 8u, now,
                                            now - min_60m + 1u, min_60m));
    CHECK(bseed_router_queue_stall_evidence(8u, 8u, now,
                                           now - min_60m, min_60m));

    CHECK(bseed_router_joined_rescue_eligible(false, false, 2u, 2u, now,
          now - min_3h, now - min_90m, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(true, false, 2u, 2u, now,
          now - min_3h, now - min_90m, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(false, true, 2u, 2u, now,
          now - min_3h, now - min_90m, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(false, false, 1u, 2u, now,
          now - min_3h, now - min_90m, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(false, false, 2u, 2u, now,
          now - min_3h + 1u, now - min_90m, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(false, false, 2u, 2u, now,
          now - min_3h, now - min_90m + 1u, min_3h, min_90m, true));
    CHECK(!bseed_router_joined_rescue_eligible(false, false, 2u, 2u, now,
          now - min_3h, now - min_90m, min_3h, min_90m, false));

    /* Wrapping hal_millis must not suppress validated elapsed time. */
    CHECK(bseed_router_timeout_evidence(4u, 4u, 1000u,
          (uint32_t)(1000u - min_20m), min_20m));
    CHECK(bseed_router_joined_rescue_eligible(false, false, 2u, 2u, 500u,
          (uint32_t)(500u - min_3h), (uint32_t)(500u - min_90m),
          min_3h, min_90m, true));
    return 0;
}
"""


def test_router_policy_executes_compiled_state_gates(tmp_path):
    compiler = shutil.which("cc")
    assert compiler, "GitHub-hosted CI requires a native C compiler"
    harness = tmp_path / "router_policy.c"
    binary = tmp_path / "router_policy"
    harness.write_text(PROGRAM, encoding="utf-8")
    built = subprocess.run(
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT),
         str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=25,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True,
                            text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_telink_driver_uses_verified_policy():
    assert POLICY.is_file()
    source = NETWORK.read_text(encoding="utf-8")
    assert '#include "router_recovery_policy.h"' in source
    assert "bseed_router_timeout_evidence(" in source
    assert "bseed_router_queue_stall_evidence(" in source
    assert "bseed_router_joined_rescue_eligible(" in source
    assert "bseed_router_rejoin_eligible(" in source
