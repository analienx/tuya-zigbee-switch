"""Event-level 80-Router / 20-Client Zigbee recovery cohort model.

This is an explicit *assumption-based* hosted CI simulation, not a Telink RF
emulator or physical child-retention measurement. Its jitter is obtained by
compiling the actual production C hash, not reimplementing it in Python.
"""
from pathlib import Path
import json
import os
import math
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTAGE_MS = 8 * 60 * 60 * 1000
ROUTERS = 80
CLIENTS = 20
CHILDREN_PER_ROUTER = 2
ROUTER_ACTIVE_SCAN_MS = 30_000  # explicit worst-case model assumption
CLIENT_ACTIVE_SCAN_MS = 20_000  # SDK duration not proven
SCAN_REPEAT_MS = 5 * 60_000  # hypothetical autonomous retry; NOT shared core
CHILD_POLL_MS = 60_000
LOST_POLL_LIMIT = 3


def _production_jitters(tmp_path):
    cfile = tmp_path / "cohort_hash.c"
    binary = tmp_path / "cohort_hash"
    cfile.write_text(r"""
#include <stdio.h>
#include <stdint.h>
#include "hal/bseed_recovery_core.h"
int main(void) {
    uint8_t ieee[8]={0xa4,0xc1,0x38,0xcd,0xf9,0x63,0xea,0u};
    for(unsigned i=0;i<80u;i++) {
        ieee[7]=(uint8_t)i;
        printf("r %u %u %u %u\n",i,
            bseed_recovery_action_stagger_ms(ieee,0x52524a4eu,900000u),
            bseed_recovery_action_stagger_ms(ieee,0x52524254u,900000u),
            (((uint32_t)ieee[1]<<16)|((uint32_t)ieee[4]<<8)|ieee[7])%900000u);
    }
    for(unsigned i=0;i<20u;i++) {
        ieee[7]=(uint8_t)(i+80u);
        printf("c %u %u %u %u\n",i,
            bseed_recovery_action_stagger_ms(ieee,0x4349534fu,900000u),
            bseed_recovery_action_stagger_ms(ieee,0x43425254u,900000u),0u);
    }
    return 0;
}
""", encoding="utf-8")
    built = subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I",
         str(ROOT / "src"), str(cfile), "-o", str(binary)],
        capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    data = []
    for line in ran.stdout.splitlines():
        role, idx, rejoin, reboot, legacy = line.split()
        data.append((role, int(idx), int(rejoin), int(reboot), int(legacy)))
    assert len(data) == ROUTERS + CLIENTS
    return data


def _overlap_peak(intervals):
    events = []
    for start, duration in intervals:
        events.append((start, +1))
        events.append((start + duration, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # half-open intervals
    current = peak = 0
    for _, change in events:
        current += change
        peak = max(current, peak)
    assert current == 0
    return peak


def _max_starts(starts, width_ms):
    starts = sorted(starts)
    left = 0
    peak = 0
    for right, time in enumerate(starts):
        while time - starts[left] >= width_ms:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def _quantile(values, fraction):
    sorted_values = sorted(values)
    return sorted_values[math.ceil(fraction * len(sorted_values)) - 1]


def _model(rows):
    scans = []
    starts = []
    reboots = []
    recovery_latencies = []
    expected_child_retained = 0
    missed_polls = 0
    endpoint_verified = 0

    for role, idx, rejoin_jitter, reboot_jitter, legacy in rows:
        if role == "r":
            # 4 actual 30s-timeout probes on a 6m+0-2m cadence;
            # 40 active Routers have a 60m child-service hold.
            busy = idx % 2 == 0
            detection = 4 * (6 * 60_000 + (idx % 120_000)) + 30_000
            hold = 60 * 60_000 if busy else 20 * 60_000
            first = max(detection, hold + rejoin_jitter)
            duration = ROUTER_ACTIVE_SCAN_MS
            # A quiet quarantined Router with no inbound child traffic
            # could earn a *single* checkpoint after the 3h hold+jitter.
            if not busy:
                reboots.append(max(first, 3 * 60 * 60_000 + reboot_jitter))
        else:
            detection = 2 * (10 * 60_000 + (idx * 67 % 30_001)) + 30_000
            first = max(detection, 20 * 60_000 + rejoin_jitter)
            duration = CLIENT_ACTIVE_SCAN_MS

        # Autonomous SDK repetition is an assumption, not observed SDK
        # timing. Evaluate radio occupancy during an 8h coordinator outage.
        for event in range(first, OUTAGE_MS + 2 * SCAN_REPEAT_MS,
                           SCAN_REPEAT_MS):
            scans.append((event, duration))
            starts.append(event)

        # Conditional verified application-path recovery: next speculative
        # SDK retry after coordinator returns, plus 15s authentication and
        # 8s ZDO/endpoint+ZCL exchange. No false JOINED credit.
        next_retry = first
        if next_retry < OUTAGE_MS:
            retries = math.ceil((OUTAGE_MS - first) / SCAN_REPEAT_MS)
            next_retry = first + retries * SCAN_REPEAT_MS
        verified_at = next_retry + 23_000
        assert verified_at >= OUTAGE_MS
        recovery_latencies.append(verified_at - OUTAGE_MS)
        endpoint_verified += 1

        if role == "r":
            # Child retention is ONLY this model's missed-poll proxy.
            # Two children per Router, each polling once per 60s. Detach if
            # three consecutive hypothetical polls miss the parent during
            # a 30s scan. Real RF route churn is not represented.
            for child in range(CHILDREN_PER_ROUTER):
                phase = (idx * 811 + child * 19_001) % CHILD_POLL_MS
                consecutive = max_consecutive = 0
                for poll in range(phase, OUTAGE_MS, CHILD_POLL_MS):
                    # Scan phases are periodic; computing modulo avoids
                    # searching thousands of interval objects.
                    missed = poll >= first and (
                        (poll - first) % SCAN_REPEAT_MS < duration
                    )
                    if missed:
                        missed_polls += 1
                        consecutive += 1
                        max_consecutive = max(max_consecutive, consecutive)
                    else:
                        consecutive = 0
                if max_consecutive < LOST_POLL_LIMIT:
                    expected_child_retained += 1

    router_rows = [r for r in rows if r[0] == "r"]
    legacy_starts = [r[4] for r in router_rows]  # raw IEEE-byte baseline
    new_starts = [r[2] for r in router_rows]
    report = {
        "coordinator_outage_hours": 8,
        "routers": ROUTERS,
        "clients": CLIENTS,
        "model_scan_duration_seconds": {
            "router": ROUTER_ACTIVE_SCAN_MS / 1000,
            "client": CLIENT_ACTIVE_SCAN_MS / 1000,
        },
        "model_sdk_repeat_minutes": SCAN_REPEAT_MS / 60_000,
        "new_router_first_scan_peak_10s": _max_starts(new_starts, 10_000),
        "legacy_raw_ieee_first_scan_peak_10s": _max_starts(
            legacy_starts, 10_000),
        "max_active_scans_during_outage": _overlap_peak(scans),
        "max_scan_starts_any_30s": _max_starts(starts, 30_000),
        "quiet_router_quarantine_reboots": len(reboots),
        "peak_quarantine_reboots_any_30s": _max_starts(reboots, 30_000),
        "verified_endpoint_count_model": endpoint_verified,
        "verified_endpoint_recovery_p50_seconds": _quantile(
            recovery_latencies, 0.50) / 1000,
        "verified_endpoint_recovery_p95_seconds": _quantile(
            recovery_latencies, 0.95) / 1000,
        "verified_endpoint_recovery_p99_seconds": _quantile(
            recovery_latencies, 0.99) / 1000,
        "child_poll_misses_model": missed_polls,
        "child_retained_model": expected_child_retained,
        "child_count_model": ROUTERS * CHILDREN_PER_ROUTER,
    }
    return report


def test_80_router_20_client_cohort_scans_reboots_and_recovery(tmp_path):
    report = _model(_production_jitters(tmp_path))
    (tmp_path / "model_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Catch another raw-IEEE burst even when a nominal 15m window exists.
    assert report["legacy_raw_ieee_first_scan_peak_10s"] == 80
    assert report["new_router_first_scan_peak_10s"] < 15
    assert report["max_active_scans_during_outage"] < 40
    assert report["peak_quarantine_reboots_any_30s"] < 20
    assert report["verified_endpoint_count_model"] == 100
    assert report["verified_endpoint_recovery_p99_seconds"] < 20 * 60
    assert report["child_retained_model"] >= 150
    # Hosted test logs can report the model; these values are conditional
    # simulated quantities, NOT a physical Zigbee/relay release gate.
    print("BSEED_RECOVERY_COHORT_MODEL " + json.dumps(report, sort_keys=True))
    # Also publish the conditional model as a visible hosted Actions summary.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n## BSEED conditional 80-Router recovery model\n")
            handle.write(
                "This is an assumption-based event simulation, NOT actual RF "
                "or relay-continuity acceptance.\n\n"
            )
            handle.write("| Metric | Model output |\n|---|---:|\n")
            for metric, value in report.items():
                if isinstance(value, dict):
                    continue
                handle.write(f"| `{metric}` | {value} |\n")
