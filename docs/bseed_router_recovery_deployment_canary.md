# BSEED Router recovery — controlled hardware acceptance

**Purpose:** prove recovery **without a mains power cycle**, not just that
Zigbee2MQTT reports OTA transport OK. Applies to draft PR #74.

## Source and target gates

Use an exact-commit GitHub-hosted Actions candidate. Require **all**: `test`
(including native budget fault injection), lint, TC32 Router compiler, PM and
non-PM paired role matrices, TS0726 candidate build when that board is
involved, candidate-finalization and OTA embedded-identity checks. A red,
queued, skipped-mandatory or unrelated-SHA job is NOT accepted. Keep Silicon
Labs deployment **separately blocked** until its official SLC/SDK job compiles
and a dedicated EFR32 Router is tested.

| Target physical model | Candidate software build | OTA image type | file version |
|---|---|---:|---|
| TS011F-BS non-PM Router | `1.1.3-bseedr13` | 43555 | `0x11023018` |
| TS011F-BS-PM Router | `1.2.5-bseedr14` | 43556 | `0x1205301C` |
| TS0726-3-BS Router | `1.1.9-bseedrv1` | 45577 | `0x11023010` |

Before any flash: independently confirm the exact IEEE, hardware
manufacturer/model and actual Router role **on the wire**, firmware source
build, board identity, image header and SHA; choose **one physically unloaded
and presently working** Router with an approved recovery route, verified
backup, and no conflicting Zigbee OTA/flash lock anywhere in the home.
Do not select a device still experiencing an unresolved OTA or a missing
source/identity proof. Never modify a fleet-wide image index or restart
Zigbee2MQTT, the coordinator, or the radio for this canary.

Follow `skills/bseed-zigbee-ota/SKILL.md` and
`docs/bseed_targeted_ota_runner.md`: private per-target campaign profile,
one-entry index, read-only `prepare/preflight/check`, and only an expressly
authorized **same-role** `flash --confirm-ieee` with exact candidate hash.
Observe the existing global OTA ownership state. Never use `status:ok`
or `100%` as acceptance: require exact installed build, successful
target-only re-interview and live Zigbee endpoint reads, before running any
recovery experiment.

## Hardware recovery experiment (no mains switching)

Record **on-device** and **coordinator** observations separately. Do not
claim the Router was isolated merely because Zigbee2MQTT stopped receiving
updates. Use a safe, approved RF test fixture/attenuation and/or passive
routing path interruption **without touching the Zigbee network key,
factory-resetting, opening permit-join or power-cycling the Router**.
Avoid disrupting child-bearing mesh Routers or attached critical appliances.

1. Record a baseline with fresh coordinator-originated Basic/OnOff (and PM
   Electrical Measurement/Metering where present) responses and device-origin
   reports. Capture actual Router role, IEEE, NWK, neighboring child traffic,
   physical relay state, PM cumulative energy, installed build ID and
   watchdog uptime/diagnostics.
2. Make **the selected target's coordinator route** unavailable without
   disconnecting the device's mains and without inducing a household-wide
   outage. Record the on-device coordinator probe timeouts and rejoin
   attempts if telemetry is available. Ensure that normal local child
   traffic is not misclassified as coordinator reachability.
3. Restore RF and observe for autonomous recovery. Require the *same* IEEE,
   secured network association, no factory reset, no new permit-join
   requirement, and **fresh successful coordinator-originated ZCL reads**
   and device-originated reports. Inbound-only announcements or a stale
   Zigbee2MQTT cache do NOT prove recovery.
4. A Router serving other devices should defer disruptive rejoin until the
   long guard, with 0–15m IEEE jitter. Confirm no repeated active scans or
   frantic announcements, and that neighbor/child routing continues.
5. Test a genuine **NOT_JOINED** SDK recovery separately and verify an
   orphaned one-shot never overlaps backoff. Check last-resort reboot budget
   stays bounded across controlled software boots and invalid NVM fails
   closed. **Never write/corrupt production NVM to force a test**; use
   the native C fault-injection gate for corruption scenarios.
6. For PM, confirm metering still reports plausible voltage, power, current
   and monotonic cumulative energy, with a known safe no-load/load-to-zero
   fixture. Do not use an energized household appliance for the test.
7. Verify pause/resume of a live OTA **does not provoke rejoin, route probes
   or rescue reboot**. Do not run a second OTA with an existing campaign lock.
8. Hold a post-recovery soak (24h preferred), including repeat bidirectional
   reads, source-route success, mesh neighbor activity and absence of
   unnecessary reboot. Record what was actually observed.

**PASS:** exact installed firmware, correct Router role, preserved IEEE,
relay/bindings/metering, autonomous restored bidirectional communication
without power cycling, no factory reset, no interfering OTA, no disruptive
retry loop, adequate soak. **FAIL/BLOCK:** coordinator-only outage,
missing on-device isolation proof, read-only coordinator route errors
without device failure, stale Z2M interview/build, incomplete OTA,
missing parent/child checks, or recovery only after mains reset.

On failure, do not auto-flash more Routers. Save private evidence and
investigate SDK callbacks, coordinator source-route state and safe return
to the last hardware-proven image. GitHub CI artifacts are candidates
only, not hardware-accepted releases.

Fleet rollout is a **separate approval**: one canary does not authorize
60+ Routers. Stage by board family and physical location with deliberate
observation; never update multiple mesh dependencies simultaneously.
