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

Exact-source CI and sealing evidence will be recorded after the candidate gates
pass. These are offline code/build checks; physical parent removal and rejoin,
fresh post-OTA boot identity, relay/load behavior, metering retention, and soak
remain separate device-specific acceptance gates. No flashing is part of this change.
