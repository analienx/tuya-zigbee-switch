# BSEED recovery PR-A: current implementation and release limits

PR #114 uses software acceptance for review and merge, following the owner's
2026-11-07 instruction to proceed without noncritical hardware. Hardware
evidence will come from production deployment. See
[software acceptance](RECOVERY_PR114_SOFTWARE_ACCEPTANCE.md).
Recovery supervision, Client signal-floor selection and
coordinator-only metering report coalescing are integrated into the Telink
firmware. This supersedes the earlier statement that none of the changes were
wired in. It does **not** establish hardware acceptance or completion of every
item in the original rev-3 plan.

## Actual recovery owner

Production retains the vendor `zb_rejoinReqWithBackOff()` for previously joined
devices and the guarded Router secure one-shot. Shared probe/hash helpers are
used in production; the standalone `claim/finish/rung` scheduler is still
auxiliary code. Its synthetic generation tests do not validate real SDK callback
attribution. There is no claim that the proposed alternating secure-channel
ladder or five-minute retry cap is the running SDK policy.

Round 8 removed the permanent one-operation-per-boot restriction: after a
terminal callback, recovery may re-arm following five minutes continuously
IDLE without an outstanding owner or scheduled reboot. Failed factory-new
steering uses 60 seconds plus an IEEE stagger of up to 30 seconds. Missing
terminal callbacks retain their watchdog/checkpoint path. Router one-shots
retain their 30-minute cooldown. Client rejoin deferred by an outstanding
coordinator probe resumes two minutes after the probe deadline, outside OTA.

**Pinned-SDK callback contract and source guard:** Telink V3.7.2.0
`bdb_zdoStartDevCnf()` posts an untagged `BDB_STATE_REJOIN_DONE` through the
BDB task, and `bdb_topLevelCommissiongConfirm()` calls our callback with
`NULL` operation context. `ZDO_NETWORK_LOST` maps to `PARENT_LOST` even though
that result ends an underlying device-start attempt. A delayed SDK init result
must not clear a live rejoin or one-shot owner. When BDB emits `SUCCESS` during
REJOIN but `zb_isDeviceJoinedNwk()` remains false, the HAL now refuses to
release that owner, avoiding the provable stale-success-before-join case.
Production-region hosted native regressions and a pinned SDK source-contract
test cover these guards.

**Remaining SDK limitation:** Neither a 5-minute quiet period nor JOINED=true
proves which SDK operation produced an old BDB SUCCESS callback. The SDK still
provides no operation ID and `zb_isDeviceJoinedNwk()` cannot disambiguate an
old completion arriving after a newer, genuinely joined attempt. The Client
NodeDesc local deadline + 2-minute drain grace is likewise not a proven SDK
transaction-cancellation boundary. These mitigations **do not close full
SDK ownership/quiescence** or demonstrate a safe five-minute secure-scan
ladder. Do not restore permanent one-operation-per-boot quarantine: it creates
an hours-long liveness trap. Require the pinned-SDK on-radio repeated-outage
trials and an SDK attribution/quiescence proof before certifying that claim.
See [the unexecuted physical acceptance protocol](RECOVERY_PR114_PHYSICAL_ACCEPTANCE.md).

## Client parent selection

`link_policy.h` contains the pure selection rules. Immediately before an
eligible Client backoff starts, neighbour admission requires LQI 118. The
superloop relaxes this to 94 at 30 seconds and the SDK default 69 at 90 seconds.
These are elapsed-time windows, **not** a measurement of individual SDK scans.
Success, request rejection, failure indication and explicit leave restore 69;
OTA restores it on the next superloop pass. A missing callback cannot keep a
strong floor indefinitely. Boot initializes 69, and Routers never change it.

Previous-parent preference additionally requires a known SDK link cost at most
2 and the existing repeated-loss policy to allow it. Unknown/poor parents are
not preferred. Coordinator isolation can request a rescan without counting a
parent MAC loss. Basic 0xFF13 byte 8 now gives the active floor for Clients;
its existing Router rescan counter meaning is unchanged.

## Coordinator-only telemetry hold

`zigbee_telemetry.c` wraps SDK `zcl_report` and `zcl_reportAttrs` references at
link time. Only standard server attribute reports in Electrical Measurement
(0x0B04) and Metering (0x0702) qualify. Failed coordinator probes, sustained
request rejection or being unjoined latch the hold; only a subsequent matched
coordinator reply releases it. JOINED or local enqueue success cannot do so.

During the hold, bindings are enumerated without changing the table. Reports
to known coordinator 0x0000 destinations are coalesced; other IEEE recipients
and groups are sent normally. Relay/level reports, commands, reads, protection
and local buttons keep their original path. Unknown IEEE destinations and
attributes that cannot be safely queued retain the original send behavior.

A bounded 32-entry RAM table stores endpoint/profile/attribute identities,
not historical measurement values. After recovery, a full-IEEE stagger of
0–60 seconds precedes a flush of current attribute values, at most one per
second. Removed bindings are not resurrected. Local rejection retains the
entry for retry; local success is not claimed as an APS delivery confirmation.
There are no extra flash writes or changes to energy values/divisors.

## Persistence, rollback and evidence

Item 70 remains four bytes. B5-to-B6 migration preserves consumed reboot
credits and verifies the CRC-protected write. A storage error cannot consume
item-71 slow-lane credit; only valid ordinary-budget exhaustion can. Item 71
stores cumulative six-hour wedge-uptime chunks; item 72 records a bounded last
checkpoint reason. Lost uncheckpointed uptime can delay recovery. There is no
guaranteed 24-hour wall-clock bound.

Historical r19 understands B5 only and rejects B6 fail-closed. Direct downgrade
to r19 is therefore not a supported automatic rollback after migration. Use a
B6-aware rollback or supervised bench recovery; never rewrite the record to
manufacture credits or erase network/application data.

Hosted tests execute the production Client policy/adapter, actual NVM modules,
and real report wrappers using the linker's `--wrap` mechanism. Recipient
isolation, coalescing, current-value flush, unbinding and proof-only hold
release are checked. PM matrix runs include these tests. All six firmware
roles must compile at the final source SHA on GitHub-hosted runners.

## Release scope still open

- Real SDK callback/transaction boundaries and repeated-outage recovery.
- Independent electrical continuity under software/watchdog/OTA reboot on the
  exact PM board, with energy, relay configuration, bindings and keys retained.
- Actual RF/child retention and recovery latency; the cohort model is conditional
  on its stated assumptions, not a physical mesh measurement.
- Remaining original-plan work: unified one-shot scheduler integration,
  persisted connected-credit refill changes, proactive Client movement limits,
  first-verified-contact build report, and runner/extension changes. These were
  not supplied by the Round 8 changes or the signal-floor/telemetry follow-up.

Candidate allocations remain unsealed: PM r22/cli25 at 0x12053024, non-PM
r19/c17 at 0x1102301e, TS0726 rv5/cli7 at 0x11023016. Old TS0726 rv2/cli4
reservations are retired. No image, index or device is promoted by this document.
Table Left's installed r19 remains a separate critical-load incident; no code
commit establishes that it is reachable or safe to reset.
