"""Compile and execute the actual mains-client poll-control code with SDK fakes."""
from pathlib import Path
import subprocess


def test_poll_failures_drift_and_rejoin_preserve_requested_mode(tmp_path):
    source = Path('src/telink/hal/zigbee_network.c').read_text()
    block = source.split('#ifdef BSEED_MAINS_CLIENT', 1)[1].split('#endif', 1)[0]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef unsigned char u8;
typedef struct {void (*handler)(void *); void *arg;} hal_task_t;
#define RESPONSE_POLL_RATE 250u
#define RET_OK 0
static uint32_t rate, delay, calls, failures;
static u8 zb_setPollRate(uint32_t r) {
    calls++;
    if (failures) {failures--; return 1;}
    rate = r; return 0;
}
static uint32_t hal_zigbee_get_poll_rate_ms(void) {return rate;}
static void hal_tasks_schedule(hal_task_t *t, uint32_t ms) {delay=ms;}
'''
    checks = r'''
int main(void) {
    configure_mains_client_keepalive();
    assert(rate == 60000 && delay == 60000);
    uint32_t before = calls;
    keepalive_verify_handler(0);
    assert(calls == before); /* no unnecessary poll reset */
    rate = 1000; failures = 1;
    keepalive_verify_handler(0);
    assert(rate == 1000 && delay == 5000);
    keepalive_verify_handler(0);
    assert(rate == 60000 && delay == 60000);
    failures = 1;
    hal_zigbee_set_ota_poll_active(true);
    assert(rate == 60000 && delay == 1000);
    keepalive_verify_handler(0);
    assert(rate == 250);
    configure_mains_client_keepalive(); /* same function as rejoin callback */
    assert(rate == 250 && delay == 1000);
    rate = 60000;
    keepalive_verify_handler(0);
    assert(rate == 250); /* repair SDK drift while OTA active */
    hal_zigbee_set_ota_poll_active(false);
    assert(rate == 60000 && delay == 60000);
    return 0;
}
'''
    executable = tmp_path / 'poll-test'
    subprocess.run(['cc', '-std=c99', '-x', 'c', '-', '-o', str(executable)],
                   input=harness+block+checks, text=True, capture_output=True, check=True)
    subprocess.run([str(executable)], check=True, timeout=5)
