"""Silicon Labs join activity through the real HAL functions (hosted C).

The network indicator blinks from these reports, so they must follow what
the stack accepted: an accepted steering start reports PAIRING and its
completion IDLE; a rejected start reports nothing; both secure-rejoin starts
(the Router recovery tick's first rejoin and the steering-path retry) share
one helper that reports REJOINING only when the stack accepted the request.
"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SILABS = ROOT / "src/silabs/hal/zigbee.c"


def _between(source, start, end):
    return start + source.split(start, 1)[1].split(end, 1)[0]


PRELUDE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint32_t sl_status_t;
#define SL_STATUS_OK 0u
#define SL_STATUS_BUSY 4u
typedef enum { SL_ZIGBEE_NO_NETWORK, SL_ZIGBEE_JOINING_NETWORK,
               SL_ZIGBEE_JOINED_NETWORK } sl_zigbee_network_status_t;
#define SL_ZIGBEE_REJOIN_DUE_TO_APP_EVENT_1 1
#define SL_ZIGBEE_DEVICE_TYPE_UNCHANGED 0
typedef enum { HAL_ZIGBEE_JOIN_ACTIVITY_IDLE, HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING,
               HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING } hal_zigbee_join_activity_t;
typedef void (*hal_join_activity_callback_t)(hal_zigbee_join_activity_t);
#define SILABS_ROUTER_REJOIN_GAP_MS (30u * 60u * 1000u)

static uint32_t now;
static uint32_t hal_millis(void) { return now; }
static sl_zigbee_network_status_t net_state = SL_ZIGBEE_NO_NETWORK;
static sl_zigbee_network_status_t sl_zigbee_af_network_state(void) { return net_state; }
static uint8_t channel = 15;
static uint8_t sl_zigbee_get_radio_channel(void) { return channel; }
static sl_status_t rejoin_result, steering_result;
static unsigned rejoin_requests, steering_requests, status_notifications;
static bool steering_completes_synchronously;
static sl_status_t sl_zigbee_find_and_rejoin_network(bool secure, uint32_t mask,
                                                     int reason, int type) {
    assert(secure && mask == (1u << channel));
    (void)reason; (void)type;
    rejoin_requests++;
    return rejoin_result;
}
void sl_zigbee_af_network_steering_complete_cb(sl_status_t status,
                                               uint8_t totalBeacons,
                                               uint8_t joinAttempts,
                                               uint8_t finalState);
static sl_status_t sl_zigbee_af_network_steering_start(void) {
    steering_requests++;
    if (steering_result == SL_STATUS_OK && steering_completes_synchronously)
        sl_zigbee_af_network_steering_complete_cb(0x80u, 0, 0, 0);
    return steering_result;
}
static void notify_network_status_change(void) { status_notifications++; }

static bool     silabs_router_rejoin_attempted;
static bool     silabs_router_ota_transfer_seen;
static bool     silabs_router_rejoin_pending;
static bool     silabs_router_rejoin_orphaned;
static uint8_t  silabs_router_rejoin_down_events;
static uint32_t silabs_router_last_rejoin_ms;
"""

CASES = r"""
static unsigned reports[3];
static void record(hal_zigbee_join_activity_t a) { reports[a]++; }

int main(void) {
    hal_register_on_join_activity_callback(record);

    /* Accepted steering start: PAIRING; completion: IDLE. */
    hal_zigbee_start_network_steering();
    assert(steering_requests == 1u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING] == 1u);
    sl_zigbee_af_network_steering_complete_cb(0x80u, 0, 0, 0);
    assert(reports[HAL_ZIGBEE_JOIN_ACTIVITY_IDLE] == 1u && status_notifications == 1u);

    /* Rejected steering start: no PAIRING. */
    now += NETWORK_STEERING_RETRY_MS;
    steering_result = SL_STATUS_BUSY;
    hal_zigbee_start_network_steering();
    assert(steering_requests == 2u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING] == 1u);

    /* Accepted start that completed inside the call: IDLE, never a late PAIRING. */
    now += NETWORK_STEERING_RETRY_MS;
    steering_result = SL_STATUS_OK; steering_completes_synchronously = true;
    hal_zigbee_start_network_steering();
    assert(steering_requests == 3u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_PAIRING] == 1u);
    assert(reports[HAL_ZIGBEE_JOIN_ACTIVITY_IDLE] == 2u);

    /* Secure rejoin helper (shared by the recovery tick's first rejoin):
     * accepted reports REJOINING, rejected and invalid channel do not. */
    rejoin_result = SL_STATUS_BUSY;
    assert(!silabs_router_start_secure_rejoin());
    assert(rejoin_requests == 1u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 0u);
    assert(!silabs_router_rejoin_pending);
    channel = 5;
    assert(!silabs_router_start_secure_rejoin());
    assert(rejoin_requests == 1u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 0u);
    channel = 15; rejoin_result = SL_STATUS_OK;
    assert(silabs_router_start_secure_rejoin());
    assert(silabs_router_rejoin_pending && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 1u);

    /* Steering-path retry after an attempted Router rejoin uses the same
     * helper and reports once per accepted request. */
    silabs_router_rejoin_attempted = true; silabs_router_rejoin_pending = false;
    now += SILABS_ROUTER_REJOIN_GAP_MS;
    hal_zigbee_start_network_steering();
    assert(rejoin_requests == 3u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 2u);
    /* Pending rejoin: no new request, no report. */
    hal_zigbee_start_network_steering();
    assert(rejoin_requests == 3u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 2u);
    /* Invalid channel on the steering path: no request and no stamped gap,
     * so the next call can retry at once. */
    silabs_router_rejoin_pending = false; now += SILABS_ROUTER_REJOIN_GAP_MS;
    channel = 5;
    uint32_t stamp = silabs_router_last_rejoin_ms;
    hal_zigbee_start_network_steering();
    assert(rejoin_requests == 3u && silabs_router_last_rejoin_ms == stamp);
    channel = 15;
    hal_zigbee_start_network_steering();
    assert(rejoin_requests == 4u && reports[HAL_ZIGBEE_JOIN_ACTIVITY_REJOINING] == 3u);
    return 0;
}
"""


def test_silabs_join_activity_follows_accepted_requests(tmp_path):
    source = SILABS.read_text(encoding="utf-8")
    storage = _between(source, "static bool     network_steering_in_progress = false;",
                       "/* A full Silicon Labs Router")
    helper = _between(source, "static bool silabs_router_channel_valid(uint8_t channel) {",
                      "void silabs_router_recovery_tick(void) {")
    register = _between(source, "void hal_register_on_join_activity_callback(",
                        "void sl_zigbee_af_stack_status_cb")
    steering = _between(source, "void hal_zigbee_start_network_steering() {",
                        "static uint8_t make_frame_control")
    path = tmp_path / "silabs_join_activity.c"
    binary = tmp_path / "silabs_join_activity"
    path.write_text(PRELUDE + storage + helper + register + steering + CASES,
                    encoding="utf-8")
    built = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-unused-function",
                            "-Wno-unused-variable", str(path), "-o", str(binary)],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_silabs_has_one_secure_rejoin_start():
    source = SILABS.read_text(encoding="utf-8")
    # Every secure rejoin request goes through the reporting helper.
    assert source.count("sl_zigbee_find_and_rejoin_network(") == 1
    tick = source.split("void silabs_router_recovery_tick(void) {", 1)[1].split(
        "bool sl_zigbee_af_pre_zdo_message_received_cb", 1)[0]
    assert "silabs_router_start_secure_rejoin()" in tick
    steering = source.split("void hal_zigbee_start_network_steering() {", 1)[1].split(
        "// Network steering complete callback", 1)[0]
    assert "silabs_router_start_secure_rejoin()" in steering
    assert re.search(r"else if \(network_steering_in_progress\)\s*\{[^}]*PAIRING", steering)
