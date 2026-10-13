# BSEED Client parent recovery corrections

The full review of merged commit `d0d2e28a15e4f99efe81f2bd942265752681d987`
found that disconnected Clients could not expire a lost one-shot completion,
unrelated ZCL traffic could mask failed parent polls, and role-marker read errors
could skip a Router-to-Client transition. Both metering variants were affected.

## Recovery lifecycle

Stale-parent detection now requests the same SDK-owned rejoin-with-backoff as
ordinary parent loss. The pinned Telink V3.7.2.0 API clears JOINED on accepted
start and continues retrying until success; application code does not need a
terminal callback to schedule the next retry. This removes the finite one-shot
flag and unreachable timeout rather than forcibly releasing an SDK operation
that might still be active. Ownership is claimed before the SDK call, rolled
back on rejected start, and retained across intermediate recovery failures.
Success observed from SDK joined state restores the Client health epoch and
keepalive, and counts once even when a BDB callback arrives later.

Only successful parent poll confirmations (including MAC NO_DATA) refresh the
parent-health clock. AF/ZCL reception still invokes its application activity
callback, but broadcast, group, and unicast reception do not clear failed-poll
evidence or recovery ownership. Quiet Clients without explicit poll failures
do not start recovery. OTA fast polling still suppresses new health-triggered
recovery, without replacing an already SDK-owned operation.

Recovery retains its four-failure threshold, twelve-minute parent silence,
and fifteen-minute cooldown. FNV-1a over all eight IEEE bytes spreads the
silence threshold across 120 seconds. The final health timer interval preserves
the stagger rather than rounding every recovery to a minute boundary. No
additional NVM writes are needed for recovery or jitter.

## Role-marker failures

NOT_FOUND initializes a new marker; ERROR preserves the existing marker and
Zigbee state. Three five-second task retries allow a transient storage fault to
recover. Persistent failure leaves Client startup blocked without an automatic
reboot loop or blind network erase. A failed marker-initialization write also
blocks startup and retries. The application loop and metering checkpoint do
not access uninitialized modules while startup is blocked or a selective role
reset is awaiting its deliberate reboot. A valid role transition still uses
the existing selective Zigbee reset and preserves application NVM/frame counters.

## Release and verification

The canonical allocator ran on GitHub-hosted Actions before source edits:
[allocation run](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38370452514).
Fresh native candidates use the board-wide versions it returned:

| Board | Router | Client | Native version |
| --- | --- | --- | --- |
| PM TS011F-BS-PM | `1.2.5-bseedr16`, type 43556 | `1.2.5-bseedcli19`, type 65024 | `0x1205301e` |
| Non-PM TS011F-BS | `1.1.3-bseedr15`, type 43555 | `1.1.3-bseedc13`, type 65026 | `0x1102301a` |

Build date is pinned to `20261013`; earlier sealed identities remain immutable.
Executable regressions use the real recovery, AF, role, application-init, and
application-loop functions with explicit SDK/NVM/task fault injection. They
run for both PM and non-PM and are included in the required PM Client matrix.
Cases cover missing/delayed/synchronous completion, SDK retry ownership,
failed starts, cooldown and clock-wrap boundaries, inbound traffic, healthy
quiet operation, OTA transitions, jitter, storage errors, and role-reset failures.

## Exact-source evidence and review

Reviewed firmware source SHA: `86dbb2640e7d7aee72682d0ec3c93410af61686b`.
[PR #84](https://github.com/analienx/tuya-zigbee-switch/pull/84) fixes all six
findings from the full review. The final source pass also guarded SDK backoff
against unrelated commissioning outcomes and tested a second parent loss after
successful recovery. This was the primary agent's source review, not an
independent second reviewer sign-off.

All required GitHub-hosted checks passed on this exact source:

- [Test/lint](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780240):
  1,146 tests, including 42 PM/non-PM recovery and startup fault cases.
- [PM native role matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780314):
  Router and Client from the same clean source, with identical clean rebuilds.
- [Non-PM native role matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780334):
  both roles and reproducibility.
- [Paired Router TC32 gate](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780308):
  both boards plus the shared contract aggregation.
- [Experimental Client builds](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780197).
- [Candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38372780208):
  authenticated prerequisite conclusions, native identity/integrity checks,
  runner-produced immutable registry proposal and four-variant package.

The proposal SHA-256 was
`06d8f8ebef702ebf32c3f176383c04f8bdc301a85f343d2ad4f3141770cdd761`.
Its four entries were copied from the runner's proposal without running local
sealing. Earlier registry entries and hashes are unchanged. A following registry
commit reruns the gates against the already sealed firmware bytes before merge.

| Native forward OTA | SHA-256 |
| --- | --- |
| PM Router r16 | `97f10eecee60831294adffc24288487aabab247e5738d254f4fc970da3ad7683` |
| PM Client cli19 | `2a7b802be06a02386691c64f47002d29c8724fa67b2d5dad9925de3e79798806` |
| Non-PM Router r15 | `a2f3b83523502e519fb23ed29b81ef0a8c72e34b092d7e7719a8193e031d5ffc` |
| Non-PM Client c13 | `84d5750bffdcedd490c182dec880c0c4bab3e8e78f0ef96661af6fca69dbbd17` |

Exact OTA SHA-512 values, native BIN hashes and filenames are preserved in the
identity registry and finalization's `CANDIDATE_PACKAGE.json` artifact. Native
SDK inspection from the original [review job](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38368009099)
established joined-bit clearing, SDK-owned backoff timers, and retry scheduling.
The executable regressions use SDK fakes; they do not execute radio behavior or
claim that the physical devices produced the injected faults. The post-mutation
start-error case is defensive fault injection, not a claim about the pinned
SDK's actual allocation-error return value.

These are offline code/build checks; physical parent removal and rejoin, fresh
post-OTA boot identity, relay/load behavior, metering retention, and soak remain
separate device-specific acceptance gates. No flashing is part of this change.
