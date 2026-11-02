"""Run the real side-effect-free parent/route link policy on GitHub Actions.

Compiles src/hal/link_policy.h, the same header the Telink driver calls, and
exercises triggers, hysteresis, cooldown and millisecond-timer wrap. Never run
repository tests on a workstation: they are GitHub-hosted only.
"""
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "src/hal/link_policy.h"
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"
MAKEFILE = ROOT / "src/telink/Makefile"

PROGRAM = r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "src/hal/link_policy.h"

#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "Link policy failed at line %d: %s\n", __LINE__, #test); \
    return 1; } } while (0)

static int client(uint32_t base) {
    const uint32_t min = 60u * 1000u;
    bseed_parent_policy_t p;
    memset(&p, 0, sizeof(p));

    /* Sticky by default; one or two losses keep the fast previous-parent rejoin. */
    CHECK(bseed_parent_policy_prefer_previous(&p, base));
    CHECK(!bseed_parent_policy_note_loss(&p, base));
    CHECK(!bseed_parent_policy_note_loss(&p, base + 20u * min));
    CHECK(bseed_parent_policy_prefer_previous(&p, base + 21u * min));
    /* Third loss inside the hour enters rescan mode exactly once. */
    CHECK(bseed_parent_policy_note_loss(&p, base + 40u * min));
    CHECK(p.rescan_entries == 1u);
    CHECK(!bseed_parent_policy_prefer_previous(&p, base + 41u * min));
    CHECK(bseed_parent_policy_note_loss(&p, base + 42u * min));
    CHECK(p.rescan_entries == 1u);
    /* Stays rescanning until six hours without a loss, then sticky again. */
    uint32_t last = base + 42u * min;
    CHECK(!bseed_parent_policy_prefer_previous(&p, last + 6u * 60u * min - 1u));
    CHECK(bseed_parent_policy_prefer_previous(&p, last + 6u * 60u * min));
    CHECK(p.loss_count == 0u);
    /* Losses spread wider than the window never trigger. */
    uint32_t t = last + 7u * 60u * min;
    CHECK(!bseed_parent_policy_note_loss(&p, t));
    CHECK(!bseed_parent_policy_note_loss(&p, t + 40u * min));
    CHECK(!bseed_parent_policy_note_loss(&p, t + 80u * min));
    CHECK(bseed_parent_policy_prefer_previous(&p, t + 81u * min));
    CHECK(bseed_parent_losses_recent(&p, t + 81u * min) == 2u);

    /* The driver's real order: note the loss, THEN ask for the flag. A
     * loss after 6 h of stability must return to sticky, not extend rescan. */
    bseed_parent_policy_t q;
    memset(&q, 0, sizeof(q));
    CHECK(!bseed_parent_policy_note_loss(&q, base));
    CHECK(!bseed_parent_policy_note_loss(&q, base + 1u * min));
    CHECK(bseed_parent_policy_note_loss(&q, base + 2u * min));
    CHECK(!bseed_parent_policy_prefer_previous(&q, base + 2u * min));
    uint32_t later = base + 2u * min + 7u * 60u * min;
    CHECK(!bseed_parent_policy_note_loss(&q, later));
    CHECK(bseed_parent_policy_prefer_previous(&q, later));
    CHECK(q.rescan_entries == 1u);
    /* Still rescanning when the next loss comes inside the stable window. */
    CHECK(!bseed_parent_policy_note_loss(&q, later + 1u * min));
    CHECK(bseed_parent_policy_note_loss(&q, later + 2u * min));
    CHECK(q.rescan_entries == 2u);
    CHECK(bseed_parent_policy_note_loss(&q, later + 3u * 60u * min));
    CHECK(!bseed_parent_policy_prefer_previous(&q, later + 3u * 60u * min));
    return 0;
}

static int router(uint32_t now) {
    const uint32_t min = 60u * 1000u;
    /* Reactive: two failed coordinator probes, independent of costs. */
    CHECK(!bseed_route_refresh_due(true, 1u, false, 0u, 0u, 1u, false, 0u, now));
    CHECK(bseed_route_refresh_due(false, 0u, false, 0u, 0u, 2u, false, 0u, now));
    /* Cooldown blocks both paths for one hour after any request. */
    CHECK(!bseed_route_refresh_due(false, 0u, false, 0u, 0u, 4u, true,
                                   now - 59u * min, now));
    CHECK(bseed_route_refresh_due(false, 0u, false, 0u, 0u, 4u, true,
                                  now - 60u * min, now));
    /* Proactive: worst cost held 15 min AND a good alternative neighbour. */
    CHECK(!bseed_route_refresh_due(true, 7u, true, now - 14u * min, 3u, 0u,
                                   false, 0u, now));
    CHECK(bseed_route_refresh_due(true, 7u, true, now - 15u * min, 3u, 0u,
                                  false, 0u, now));
    CHECK(!bseed_route_refresh_due(true, 7u, true, now - 15u * min, 5u, 0u,
                                   false, 0u, now));
    CHECK(!bseed_route_refresh_due(true, 7u, true, now - 15u * min, 0u, 0u,
                                   false, 0u, now));
    CHECK(!bseed_route_refresh_due(true, 5u, true, now - 15u * min, 1u, 0u,
                                   false, 0u, now));
    CHECK(!bseed_route_refresh_due(false, 7u, true, now - 15u * min, 1u, 0u,
                                   false, 0u, now));
    CHECK(!bseed_route_refresh_due(true, 7u, false, now - 15u * min, 1u, 0u,
                                   false, 0u, now));
    return 0;
}

int main(void) {
    /* Normal timeline and one straddling the 32-bit millisecond wrap. */
    if (client(1000u) || client(0xFFFFFFFFu - 30u * 60u * 1000u)) return 1;
    if (router(100000000u) || router(5u * 60u * 1000u)) return 1;
    return 0;
}
"""


def test_link_policy_executes_compiled_gates(tmp_path):
    compiler = shutil.which("cc")
    assert compiler, "GitHub-hosted CI requires a native C compiler"
    harness = tmp_path / "link_policy.c"
    binary = tmp_path / "link_policy"
    harness.write_text(PROGRAM, encoding="utf-8")
    built = subprocess.run(
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT),
         str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=25,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_driver_uses_policy_for_both_roles_and_never_broadcast_mode():
    assert POLICY.is_file()
    source = NETWORK.read_text(encoding="utf-8")
    assert '#include "hal/link_policy.h"' in source
    for call in ("bseed_parent_policy_note_loss(", "bseed_parent_policy_prefer_previous(",
                 "bseed_route_refresh_due(", "zb_routeDiscReq(&req)"):
        assert call in source, call
    # Address mode 0 is a broadcast many-to-one discovery; only 16-bit unicast is allowed.
    assert re.search(r"req\.dstAddrMode\s*=\s*ZB_ADDR_16BIT_DEV_OR_BROADCAST;", source)
    assert not re.search(r"dstAddrMode\s*=\s*SHORT_ADDR_MODE", source)
    # Sticky default after zb_init(); rescans only through policy or the Router one-shot.
    init = source[source.index("void telink_zigbee_hal_network_init(void)"):]
    assert init.index("zb_init();") < init.index("PRE_PARENT_FIRST_WHEN_REJOIN = TRUE;")
    assert len(re.findall(r"PRE_PARENT_FIRST_WHEN_REJOIN\s*=\s*FALSE;", source)) == 1
    begin = source.split("static void link_router_rejoin_begin(void) {", 1)[1].split("}", 1)[0]
    assert "PRE_PARENT_FIRST_WHEN_REJOIN = FALSE;" in begin
    tick = source.split("void telink_zigbee_hal_router_recovery_tick(void) {", 1)[1]
    body = tick.split("\n}\n", 1)[0]
    assert body.index("link_router_rejoin_begin();") < body.index("zb_rejoinReq(")
    # Count a rescan rejoin only once the SDK accepted it; restore sticky on
    # rejection and on both orphaned one-shot paths.
    assert body.index("zb_rejoinReq(") < body.index("link_router_rejoin_started(now);")
    assert body.count("link_router_rejoin_end();") == 3
    # No route discovery while an orphaned one-shot may own the radio, and no
    # rejoin scan right after a route discovery.
    assert "if (!router_one_shot_stalled)\n        link_router_route_tick(now);" in body
    assert "!link_router_rejoin_held(now)" in body
    assert body.index("link_router_route_tick(now);") < body.index("bseed_router_joined_rescue_eligible(")
    # A coordinator in the neighbour table is the next hop before any route entry.
    route = source.split("static void link_router_route_tick(uint32_t now) {", 1)[1].split("\n}\n", 1)[0]
    assert route.index("nwk_neTblGetByShortAddr(0x0000u)") < route.index("g_routingTab[i]")
    # A PARENT_LOST raised inside the SDK's own backoff is not a new loss.
    lost = source.split("case BDB_COMMISSION_STA_PARENT_LOST:", 1)[1].split("break;", 1)[0]
    assert "if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN)\n            link_note_parent_loss();" in lost
    # Recovery tick and Client keepalive block stay free of policy #ifdefs:
    # source-slicing harnesses cut these functions at their first #endif.
    assert "#ifdef BSEED_LINK_POLICY" not in body
    keepalive = source.split("#ifdef BSEED_MAINS_CLIENT", 1)[1].split("#endif", 1)[0]
    assert "MAINS_CLIENT_KEEPALIVE_POLL_MS" in keepalive


def test_policy_is_enabled_for_every_telink_build():
    make = MAKEFILE.read_text(encoding="utf-8")
    assert "BSEED_LINK_POLICY ?= 1" in make
    assert "DEVICE_DEFS += -DBSEED_LINK_POLICY" in make
    basic = (ROOT / "src/zigbee/basic_cluster.c").read_text(encoding="utf-8")
    assert "0xFF13" in basic and "firmware_link_snapshot" in basic
    header = (ROOT / "src/zigbee/basic_cluster.h").read_text(encoding="utf-8")
    assert "attr_infos[18]" in header
