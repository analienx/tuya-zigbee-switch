# BSEED recovery — Round 7 acceptance and operational limits

**Historical Round 7 record.** Round 8 replaced the permanent same-boot
quarantine and the three-hour Client ZDO deferral with bounded re-arm/drain
delays. The subsequent follow-up adds Client signal-floor selection and
coordinator-only telemetry coalescing. See
[the current implementation and limits](bseed_recovery_pr_a_design.md);
the original Round 7 behavior below must not be used as the current contract.

The owner superseded the physical-test draft gate on 2026-11-07.
[Software acceptance](RECOVERY_PR114_SOFTWARE_ACCEPTANCE.md) determines
current PR readiness, and hardware evidence will come from production deployment.
This document captures the
quarantine-liveness correction and distinguishes hosted tests from physical
evidence. It is not a claim that `LivingRoomSocketTableLeft` has recovered.

## What the firmware actually does

1. **SDK-owned Router rejoin and Client backoff remain vendor operations.**
   The shared core's secure current-/all-channel rung and synthetic generation
   token are not wired to actual Telink BDB callback ownership. Native
   tests of those helpers do not prove that the SDK supplies a callback token.
2. **An SDK callback lacks an operation ID.** After an accepted one-shot,
   backoff or factory-new steering, a new radio owner is not assigned in the
   same MCU boot. A late callback can therefore not terminate a newer owner.
3. **Quarantine is not connectivity failure.** For an IDLE/JOINED Router with
   a prior SDK owner, four independent coordinator ZDO failures (or the
   sustained local queue-rejection threshold), absence of a verified
   coordinator reply for at least 3h plus a separate IEEE-derived 0–15m
   checkpoint stagger, and at least 90m of no inbound application traffic
   are required before a quarantined checkpoint can qualify.
4. **False-JOINED flaps do not erase an observed NOT_JOINED epoch.** A
   Router with a former SDK owner, presently not joined, qualifies after a
   continuous observed outage epoch of at least 45m plus the 0–15m
   checkpoint stagger. Only a *matched real coordinator ZDO reply* (or
   factory-new reset of the observation contract) resets that epoch.
5. **One persistent-credit decision per Router tick.** An orphaned/owned
   SDK wedge or independently qualified IDLE quarantine can count actual
   wedged MCU uptime into NVM item 71. Its CRC-verified six-hour chunks
   accumulate across episodes and survive MCU restarts. With item 70
   validly exhausted, four stored chunks permit exactly one bounded
   checkpoint. Corrupt/read/write-failed item 70 remains fail-closed
   regardless of available item-71 credit. No absolute 24h wall-clock
   guarantee is possible without an RTC.
6. **No fabricated rejoin attempts.** The existing joined-rescue policy
   receives the *actual* Router one-shot count. The new IDLE-quarantine
   predicate has its own observed-negative-evidence/neighbor-grace
   contract. No one-shot, backoff or steering history is silently presented
   as two attempted rejoins.
7. **Factory-new steering:** An accepted steering A cannot be followed
   by steering B in the same boot without a correlated SDK terminal
   guarantee. After terminal failure, 45m+hash stagger is allowed; after a
   lost callback, 90m+hash stagger. One verified ordinary-budget credit
   can fund a processor checkpoint, retaining identity and relay state.
   No automatic reset storm: once the two persistent credits are exhausted,
   a supervised intervention is required. A synchronously refused SDK
   start did not acquire ownership and may retry after the existing 5s gate.
8. **Accepted Client coordinator ZDO during parent loss:** An accepted,
   outstanding NodeDesc transaction prevents SDK rejoin ownership. A
   local 30s application timeout does not establish SDK transaction
   cancellation; new application NodeDesc transmissions are held while
   this uncertainty exists. A genuinely matched coordinator reply clears
   the hold; otherwise a separate 3h+IEEE-stagger checkpoint can be
   considered through the same fail-closed NVM budget/slow credit.
   This conservative behavior does **not** claim a hardware SDK
   quiescence primitive exists.

## Hosted regression contracts

- **R7a:** previous accepted backoff, now IDLE/JOINED and 4 timed-out
  coordinator probes, item70 VALID_EXHAUSTED, item71 empty. Run four
  actual 6h persisted chunks and require one (not two) checkpoint.
- **R7b:** terminal one-shot A, later NOT_JOINED, deliberate false-JOINED
  flap. No new radio owner; a monotonic not-joined epoch and persisted
  credit lead to one bounded checkpoint.
- **R7c:** corrupt/read/write-failed item70 combined with full valid
  item71 can never reboot; matched healthy coordinator evidence suspends
  wedge credit. Persisted partial chunks survive cold MCU reset.
- **S7:** factory-new steering A→terminal failure→would-be B→late A
  callback. Prove B is not started; a long, budgeted reboot permits a
  fresh SDK owner in the next processor epoch. Include synchronous SDK
  callback and immediate request-rejection variants.
- **C7:** accepted ZDO, BDB PARENT_LOST, delayed ZDO result and
  application timeout. No overlapping SDK owner, no false coordinator
  credit; eventual bounded checkpoint under persistent budget.
- **Fleet:** compile and execute the actual shared C IEEE-jitter function
  for 80 sequential-address Routers and 20 Clients. Compare a legacy
  raw-byte 80-node burst against domain-separated full-IEEE staggering
  in an event-level overnight-outage model.

## Cohort simulation — what it can and cannot prove

`tests/test_recovery_cohort_event_model.py` models an 8-hour nightly
coordinator outage, 80 Routers and 20 mains Clients. The production-C
hash supplies actual first-action jitters. Assumptions, **not observed
SDK characteristics**: Router scan 30s, Client scan 20s, autonomous
retry every 5 minutes, Router active-child hold of 60 minutes (otherwise
20m), 15s association and 8s ZDO/endpoint+ZCL verification after
coordinator restoration. It reports maximum concurrently scanning
devices, 10/30-second scan starts, checkpoint clustering, p50/p95/p99
*modeled endpoint verification delay*, and modeled child poll misses.

A separate proxy assumes each Router has two children polling every 60s,
and only three consecutive misses detach a child. This is **not a real
Zigbee child-retention or RF interference measurement**. The simulated
retry period is illustrative and does not imply that the standalone
shared-core 5-minute retry ladder is connected to the SDK.

The model does not account for RF airtime contention, channel changes,
Zigbee NWK retransmits, coordinator queue depth, the vendor's internal
BDB scan durations/retry schedule, OTA load, Zigbee2MQTT interview
behavior, sleepy-child reparenting or wall-clock power-down of the
coordinator. An independent physical 60–80-device cohort or representative
RF canary is still required for release acceptance. Report no unconditional
3.5-minute or 24-hour recovery guarantee.

## Downgrade policy: B6 rescue budget vs older r19/B5

The new item-70 record preserves the **four-byte ABI and spent credits**
when migrating B5 to B6 CRC8. **A straight downgrade to historical r19
is unsupported as a normal automatic rollback.** r19 understands only B5
and will correctly reject B6 fail-closed: Zigbee application service
might work, but automatic rescue reboots will be disabled. Never rewrite
item70 blindly, reset Zigbee credentials, or use an unverified downgrade
tool to work around this. Rollback candidates should be B6-aware, or a
supervised bench recovery with independent power and explicitly reviewed
budget migration. No network key, radio identity, binding, PM total or
relay state should be altered by a budget-record migration.

## Physical release gates still OPEN

- Exactly matched PM hardware must demonstrate **zero output interruption**
  during controlled software reset, watchdog reset, OTA boot and NVM restore,
  measured with an **independently powered logger** on the actual relay load.
- Demonstrate actual Zigbee SDK/BDB behavior and confirmed coordinator
  NodeDesc/endpoint/ZCL telemetry following a failed callback and later
  second outage on a noncritical sacrificial socket.
- Record OTA compatibility, firmware IDs, PM energy persistence, network
  security identity, bindings and local physical control preservation.
- Measure mesh-wide scan and reboot concurrency, route/child retention,
  and p50/p95/p99 verified recovery with the actual SDK and coordinator.
- Plan an independent supply/bypass for the HA/coordinator-powered critical
  outlet before **any** physical intervention. An RF-unreachable r19 cannot
  receive a code fix merely because a PR was merged.

The historical physical acceptance decision above is superseded for source
review and merge by the owner's software acceptance scope.
