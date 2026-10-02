# Router and mains Client shared hardening

This pass follows the independent issue #55 review and applies to both socket
roles. Native candidates are PM Client cli13 / Router r11 (0x12053018) and
non-PM Client c8 / Router r11 (0x11023015), with build date 20261002.
The canonical allocator confirmed these identities on GitHub-hosted Actions
run [37053399779](https://github.com/analienx/tuya-zigbee-switch/actions/runs/37053399779)
before source changes. The retired PM .17 tuples remain reserved.

## Implemented fixes

| Finding | Scope | Behavior |
| --- | --- | --- |
| Dividing the wrapping 16 MHz register produced a clock that reset every ~268 seconds. | Both roles; all Telink boards | HAL milliseconds now accumulate the SDK's elapsed-time updates, including fractional ticks and sleep. Five-minute saves and protection/button deadlines use normal uint32 elapsed arithmetic. |
| HAL tasks could be lost when the 24 SDK timer slots were occupied. | Both roles; all Telink boards | Each application task owns its event. Self-rearm and interrupt rearm survive atomic SDK callback finalization without a second allocation. Zero-delay work yields one millisecond. Native compilation checks every event ABI field. |
| Meter data remained valid after sampling stopped. | Both PM roles | After 20 seconds without sampler progress, mark data invalid, count the stall and request bounded recovery. Discard the overdue window, retain accumulated energy, and resume after a complete five-second window. A zero-pulse idle sample is fresh. |
| Failed periodic energy writes advanced the successful-save time and were forgotten. | Both PM roles | Check write status, preserve the successful stamp, count failures and retry every 30 seconds. Retry trusted energy/reset totals even while sampling is invalid. Skip unchanged periodic writes. Controlled reboot retains write/readback verification. |
| Rejected recovery startup could be called every application iteration. | Both roles | Pace rejected steering/rejoin startup at five seconds. Preserve SDK ownership of an accepted self-sustaining rejoin/backoff. |
| Client keepalive enforcement could override SDK polling during disconnection. | Client only | Enforce 60 seconds (250 ms during OTA) only while joined. Do not alter SDK disconnected recovery rates. |
| Runtime failures lacked inspectable evidence. | Both Telink roles | Read-only Basic 0xFF10 health snapshot, RAM-only counters, one-second refresh. |

Calibration constants, SEL cadence, normal five-second sampling, mains Rx-on
configuration, normal 60-second Client keepalive and OTA block pacing remain
unchanged. Measurements accumulated during an unknown stalled window cannot
be reconstructed. Retained Electrical Measurement attributes are the last
valid values; inspect the health stale flag/age before treating them as fresh.
Meter-derived overload evaluation pauses while data is invalid; this software
is not a substitute for an independent electrical protection device.

## Health snapshot

Basic cluster 0x0000, attribute 0xFF10, ZCL octet string, 48-byte payload.
The ZCL wire representation has a leading length byte (48); offsets below
are relative to the payload after that byte. All multibyte values are
little-endian. Schema version is 1. No automatic reporting is configured,
no NVM record is written, and no remote command is needed to enable it.

| Offset | Width | Meaning |
| --- | --- | --- |
| 0 | 1 | Schema version |
| 1 | 1 | Network state: 0 disconnected, 1 joined, 2 joining |
| 2 | 1 | Flags: bit 0 meter enabled, bit 1 meter stale, bit 2 actual MAC Rx-on-when-idle |
| 3 | 1 | Last delivered NLME sync/poll confirmation status |
| 4 | 4 | Uptime milliseconds, modulo 2^32 |
| 8 / 12 | 4 each | Successful / failed delivered sync confirmations; MAC NO_DATA is success |
| 16 | 4 | Parent-loss callbacks |
| 20 / 24 / 28 | 4 each | Rejoin start attempts / start or intermediate failures / completed recoveries |
| 32 / 36 | 4 each | Sampler stalls / persistence failures |
| 40 | 4 | Sampler progress age in ms; 0xFFFFFFFF if no meter |
| 44 / 46 | 2 / 1 | Latest SDK exception source line / code |
| 47 | 1 | Reserved |

Counters saturate at 0xFFFFFFFF and reset on boot. The SDK exception record
also resets on boot; it is not a persistent reset reason. Poll counts cover
confirmations delivered to the application, not an independent radio capture.
Before the first one-second refresh the payload has only schema/length set.
Reading diagnostics is deliberate traffic and must be distinguished from
unsolicited reports during hardware acceptance.

## Validation and remaining acceptance

All repository workloads run on GitHub-hosted Actions at the exact PR head:
full host/converter tests and lint, the shared PM Router/Client matrix, non-PM
matrix, Router TC32 validation and the existing Client canary build. Tests run
the pinned SDK event implementation with a full pool, sub-millisecond ticks,
multiple hardware wraps, sleep elapsed time, self-cancel/rearm and interrupt
rearm. Shared fault injection covers stale/idle sampling, persistence failures,
reset retry, millisecond wrap and SDK-owned network recovery in both roles.

Source SHA, final run links and sealed identities will be recorded after the
final head passes. Green CI proves offline integrity/reproducibility only.
Separate authorized hardware work must still establish actual installed boot
identity, working downlink, retained energy, no-load/known-load metering,
protection timing, parent loss/rejoin, Router sleepy-child parenting, OTA
abort/apply recovery and a sufficiently long soak. Previous campaign locks
and device incident gates continue to apply.

More aggressive sampling/SEL switching, watchdog recovery from partial stack
wedges, adaptive OTA pacing and parent selection changes remain experimental.
Mains power removes a battery constraint; it does not establish RF congestion,
flash endurance or fixture accuracy. Those changes require measured hardware
evidence and are not bundled into this defect fix.
