"""SDK recovery ownership is re-armed after a terminal, quiet operation.

The once-per-boot owner guard protects against untagged late BDB callbacks,
but it must not turn every later parent loss into an hours-long outage that
only a budgeted reboot can end. These native tests run the production
re-arm function on GitHub-hosted CI for Router and mains Client builds.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "src/telink/hal/zigbee_network.c"
MAIN = ROOT / "src/telink/main.c"
HEADER = ROOT / "src/telink/hal/telink_zigbee_hal.h"

MARK = "/* Re-arm SDK recovery ownership after a genuinely finished operation."
END = "void hal_zigbee_set_poll_rate_ms"


def rearm_section():
    source = NETWORK.read_text(encoding="utf-8")
    assert source.count(MARK) == 1
    return MARK + source.split(MARK, 1)[1].split(END, 1)[0]


COMMON = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "hal/bseed_recovery_core.h"
typedef enum {
    TELINK_NETWORK_RECOVERY_IDLE = 0,
    TELINK_NETWORK_RECOVERY_STEERING,
    TELINK_NETWORK_RECOVERY_REJOIN,
} telink_network_recovery_state_t;
static telink_network_recovery_state_t network_recovery_state;
static bool     network_sdk_backoff_started_once, network_sdk_steering_started_once;
static bool     network_steering_terminal_seen, network_steering_checkpoint_scheduled;
static uint32_t network_steering_terminal_ms;
static struct { uint8_t extAddress[8]; } g_zbMacPib = {{1,2,3,4,5,6,7,8}};
static uint32_t now;
static bool     factory_new;
static uint32_t hal_millis(void) { return now; }
static bool zb_isDeviceFactoryNew(void) { return factory_new; }
#define MIN (60u * 1000u)
"""

ROUTER = COMMON + r"""
#define ZB_ROUTER_ROLE 1
#define ZB_COORDINATOR_ROLE 0
#define ROUTER_REJOIN_COOLDOWN_MS (30u * MIN)
static bool     router_one_shot_active, router_one_shot_stalled;
static bool     router_one_shot_attempted, router_reboot_scheduled;
static uint32_t router_one_shot_ms;
"""

CLIENT = COMMON + r"""
#define ZB_ROUTER_ROLE 0
#define ZB_COORDINATOR_ROLE 0
#define BSEED_MAINS_CLIENT 1
static bool client_reboot_scheduled;
"""

ROUTER_SCENARIO = r"""
static void tick_for(uint32_t ms) {
    for (uint32_t t = 0; t <= ms; t += 1000u) { telink_zigbee_hal_sdk_owner_rearm_tick(); now += 1000u; }
}
int main(void) {
    now = 1000u;
    /* 1. A live SDK backoff is never re-armed, however long it runs. */
    network_sdk_backoff_started_once = true;
    network_recovery_state = TELINK_NETWORK_RECOVERY_REJOIN;
    tick_for(3u * 60u * MIN);
    assert(network_sdk_backoff_started_once);

    /* 2. Terminal callback moved it to IDLE: re-armed only after 5 quiet min. */
    network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
    tick_for(4u * MIN + 50000u);
    assert(network_sdk_backoff_started_once);
    tick_for(15000u);
    assert(!network_sdk_backoff_started_once);

    /* 3. An orphaned (stalled) one-shot is never re-armed here. */
    router_one_shot_attempted = true; router_one_shot_stalled = true;
    router_one_shot_ms = now;
    tick_for(2u * 60u * MIN);
    assert(router_one_shot_attempted);

    /* 4. A terminated one-shot is re-armed after the quiet window, which
     *    restarts whenever an owner was live in between. */
    router_one_shot_stalled = false;
    tick_for(3u * MIN);
    router_one_shot_active = true;            /* new live owner: quiet resets */
    tick_for(MIN);
    router_one_shot_active = false;
    tick_for(4u * MIN);
    assert(router_one_shot_attempted);
    tick_for(70000u);
    assert(!router_one_shot_attempted);

    /* 5. A scheduled checkpoint reboot blocks every re-arm. */
    network_sdk_backoff_started_once = true; router_reboot_scheduled = true;
    tick_for(30u * MIN);
    assert(network_sdk_backoff_started_once);
    router_reboot_scheduled = false;
    tick_for(6u * MIN);
    assert(!network_sdk_backoff_started_once);

    /* 6. Failed factory-new steering retries after a quiet, staggered delay. */
    factory_new = true;
    network_sdk_steering_started_once = true;
    network_steering_terminal_seen = true;
    network_steering_terminal_ms = now;
    tick_for(55000u);
    assert(network_sdk_steering_started_once);
    tick_for(40000u);                         /* > 60 s + 30 s maximum jitter */
    assert(!network_sdk_steering_started_once && !network_steering_terminal_seen);

    /* 7. A steering checkpoint in progress blocks the retry. */
    network_sdk_steering_started_once = true;
    network_steering_terminal_seen = true;
    network_steering_terminal_ms = now;
    network_steering_checkpoint_scheduled = true;
    tick_for(10u * MIN);
    assert(network_sdk_steering_started_once);
    network_steering_checkpoint_scheduled = false;

    /* 8. Successful steering: no longer factory-new, re-armed after quiet. */
    factory_new = false;
    network_steering_terminal_seen = false;
    tick_for(4u * MIN);
    assert(network_sdk_steering_started_once);
    tick_for(70000u);
    assert(!network_sdk_steering_started_once);
    puts("router rearm ok");
    return 0;
}
"""

CLIENT_SCENARIO = r"""
static void tick_for(uint32_t ms) {
    for (uint32_t t = 0; t <= ms; t += 1000u) { telink_zigbee_hal_sdk_owner_rearm_tick(); now += 1000u; }
}
int main(void) {
    now = 5000u;
    /* A Client with a weak parent: backoff A succeeded, then another loss.
     * The next backoff must be possible minutes later, not after hours. */
    network_sdk_backoff_started_once = true;
    network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
    tick_for(5u * MIN + 2000u);
    assert(!network_sdk_backoff_started_once);

    /* Backoff B running: no re-arm. A checkpoint reboot also blocks it. */
    network_sdk_backoff_started_once = true;
    network_recovery_state = TELINK_NETWORK_RECOVERY_REJOIN;
    tick_for(60u * MIN);
    assert(network_sdk_backoff_started_once);
    network_recovery_state = TELINK_NETWORK_RECOVERY_IDLE;
    client_reboot_scheduled = true;
    tick_for(60u * MIN);
    assert(network_sdk_backoff_started_once);
    puts("client rearm ok");
    return 0;
}
"""


def build_and_run(tmp_path, name, prelude, scenario):
    cfile = tmp_path / (name + ".c")
    binary = tmp_path / name
    cfile.write_text(prelude + rearm_section() + scenario, encoding="utf-8")
    built = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "src"), str(cfile), "-o", str(binary)],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


def test_router_ownership_rearms_only_after_terminal_quiet_window(tmp_path):
    build_and_run(tmp_path, "router_rearm", ROUTER, ROUTER_SCENARIO)


def test_client_ownership_rearms_after_successful_backoff(tmp_path):
    build_and_run(tmp_path, "client_rearm", CLIENT, CLIENT_SCENARIO)


def test_superloop_rearms_before_role_watchdogs_and_cooldown_is_kept():
    main = MAIN.read_text(encoding="utf-8")
    rearm = main.index("telink_zigbee_hal_sdk_owner_rearm_tick();")
    assert rearm < main.index("telink_zigbee_hal_router_recovery_tick();")
    assert rearm < main.index("telink_zigbee_hal_client_recovery_tick();")
    assert "void telink_zigbee_hal_sdk_owner_rearm_tick(void);" in HEADER.read_text(
        encoding="utf-8")

    source = NETWORK.read_text(encoding="utf-8")
    # The guards stay in place; only the re-arm tick may clear them.
    assert "network_sdk_backoff_started_once || !network_start_ready()" in source
    assert "router_one_shot_attempted || network_sdk_steering_started_once)" in source
    # A later Router one-shot keeps its 30-minute cooldown.
    assert ("(router_one_shot_attempts == 0u ||\n"
            "         (uint32_t)(now - router_one_shot_ms) >= ROUTER_REJOIN_COOLDOWN_MS)"
            in source)
