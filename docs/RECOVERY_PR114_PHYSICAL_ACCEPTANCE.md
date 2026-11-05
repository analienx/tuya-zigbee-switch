# PR #114 — RF and electrical acceptance protocol (NOT YET EXECUTED)

This is an operator-owned, device-specific acceptance plan, **not** a CI
certificate. Green source builds, 1,400 host tests, OTA 100% and JOINED bits
cannot prove relay continuity, SDK callback quiescence or verified ZCL contact.
No production-critical outlet is a test target.

## Stage 0 — electrical / identity safety

- Use a separately supplied sacrificial socket of the *same board revision*,
  with no critical household load. Qualified personnel must verify the
  appropriate mains-safe instrumentation and protective arrangement.
- Before any software reboot, watchdog reset or OTA boot, connect an
  **independently powered** voltage/event logger *downstream of the relay*.
  Log time-aligned output voltage and current at sufficient resolution to
  detect subsecond interruptions; Zigbee telemetry is not an independent
  measurement. Verify that a deliberate OFF event is detectable.
- Save SHA-256, board name, bootloader and running image identity, exact
  firmware candidate SHA and OTA tuple, existing IEEE/NWK/security,
  NVM item-70/71/72 versions, binds, groups, relay mode, state and PM energy.
  Store private network credentials and captures **outside the public repo**.
- Before the critical Table Left can ever be a candidate, provide and prove an
  independent power/bypass path for Home Assistant and the coordinator.
  Do not interrupt its supply in order to test firmware recovery.

## Stage 1 — SDK callback ownership and repeated outages

Run on a noncritical Router then on a mains Client. Record serial/BDB callbacks,
actual radio TX/air captures (where feasible), coordinator ZDO probe IDs,
network status, rejoin requests, and fresh post-recovery endpoint/ZCL values.

1. Establish first verified coordinator NodeDesc roundtrip and fresh
   application report; then induce one parent loss. Observe vendor backoff
   and return, without power cycling or re-pairing.
2. Repeat losses **twice more during the same MCU uptime** after the
   quiet-period re-arm. Confirm each operation reaches a real terminal
   boundary; late duplicate A callbacks must not release active B. Include
   duplicate SUCCESS before SDK JOINED and at JOINED, delayed failure,
   missing callback, rejection and a late outstanding NodeDesc response.
   The JOINED-true duplicate case is **not distinguishable** by current HAL
   guards: explicitly fail if SDK can emit it during another active operation.
3. Induce coordinator-only route failure with a healthy parent MAC ACK.
   Confirm there is no false coordinator credit from parent polls, incoming
   neighbors, local ZDO enqueue, or generic JOINED state.
4. With failed coordinator contact, verify the Client floor 118 -> 94 -> 69
   around 0/30/90 seconds of *actual admission time*, including refused SDK
   start, OTA hold and a successful join. Check that the new parent remains
   stable and no gratuitous rejoin occurs in a healthy link.
5. Confirm PM Electrical Measurement/Metering reports to coordinator 0x0000
   coalesce only while coordinator proof is missing. Peer/group reporting,
   local relay and commands must continue. After authenticated proof,
   flush current measurements (not stale samples) at <=1 item/s with
   stable IEEE-based distribution; check binding removal, full buffer,
   and coordinator relapse mid-flush.

**Stop condition:** any overlapping vendor SDK owner, ZDO/APS memory fault,
unexpected steering/factory reset, unbounded retry, premature BDB release,
loss of local switch or unexplained power interruption.

## Stage 2 — physical relay continuity

For each exact PM board, record traces **for all** of: application-requested
software checkpoint, watchdog-triggered reboot, normal OTA boot and repeated
in-place rejoin/route repair. Test relay initially ON and OFF; verify local
button and protection immediately afterward. **Zero unintended output-voltage
interruptions** is a hard gate for critical-load suitability. An unknown
logger resolution is a failed gate, not proof of no interruption.

Verify monotonic retained PM total across restarts (no reset to zero), correct
power/current/voltage units and divisors, relay startup mode, security keys,
binding/group records and preserved endpoint identities. Repeat OTA from the
prior B6-aware candidate only; never use historical r19/B5 downgrade as a
quiet automatic recovery path after migrating item70.

## Stage 3 — fleet RF and operational safety

- On a representative mesh, replay 60-80 Router/Client owners with their
  actual configured MAC keepalive, child polling, SDK backoff and channel
  conditions, including coordinators unreachable for 8h. Record request
  concurrency, overlapping scans, child retention, missed MAC/APS responses,
  and 50th/95th/99th percentiles of **fresh endpoint/ZCL verification**
  after coordinator return. The cohort simulation's 5-minute scan interval
  is hypothetical and must not be used as measured SDK timing.
- Validate budget item70=B6 at valid/exhausted/corrupt/read-fault/write-fault,
  wedge item71 after real 6h increments, loss-of-power partial credit,
  high-uptime millis wrap and exactly one checkpoint per boot. Confirm bad NVM
  never authorizes a reboot and 12h of good coordinator service behaves as
  documented. Exempt factory-new and in-progress OTA.
- Record a separately installed, B6-aware rollback image and an operator
  recovery plan for cases where no RF route exists; a source PR cannot
  repair an unreachable r19 socket remotely.

## Evidence and disposition

For each trial preserve: test ID, board/revision, role, exact source SHA,
installed build/OTA identity, SDK tag, coordinator firmware, timestamped SDK and
Z2M logs, air-capture hash if available, independently powered relay-logger
trace hash and resolution, coordinator NodeDesc/endpoint/ZCL proofs, reboot
reason and NVM raw-state audit, expected/observed behavior and operator verdict.

Accept only if all applicable gates have independently reviewed **PASS**
evidence. Any missing trace is **UNTESTED**, not PASS. Keep PR #114 draft,
unsealed and unflashed for critical supplies until this record is complete.
