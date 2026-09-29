# Consolidated PM Client cli11 and build-process repair

> **Historical/superseded:** PR #56 has since advanced the native PM pair to
> `cli12/r9` at `0x12053016`. The version-bumped PM return candidate described
> below is retired; current cross-role hardware testing uses the private FORCE
> transport in `bseed_force_test_transition.md`. This file remains as candidate
> history and rationale, not the current build/sealing procedure.

Status: uncommitted candidate awaiting exact-source public CI; no device flashed and no
fleet acceptance claimed. This record supersedes cli10 as the proposed next
KitchenSocketLeft candidate once its CI artifacts are verified and sealed.

**Independent review HOLD (2026-09-27):** all four variants received RED in
[issue 55](https://github.com/analienx/tuya-zigbee-switch/issues/55#issuecomment-5853890662).
The [triage and remediation requirements](bseed_independent_review_triage_20260927.md)
supersede any implication below that internal review found all remaining blockers.
The reported source, build and deployment findings remain open.

**Remediation update:** the working tree now also reserves non-PM Router
`1.1.3-bseedr9` and Client `1.1.3-bseedc6`, both `0x11023013`, with native image
types 43555 and 65026. They have their own release definition, exact pin guard,
pinned date and proposed native role matrix. The legacy non-PM workflow update
is pending approval. This supersedes the earlier scope statements below that
only PM candidates were being prepared; it does not establish a non-PM release.
See the triage's remediation table for implementation status and remaining gaps.

| Artifact | Basic build ID | OTA version | Image type |
| --- | --- | --- | --- |
| PM Client | `1.2.5-bseedcli11` | `0x12053014` / 302329876 | 65024 |
| Shared-source Router | `1.2.5-bseedr7` | `0x12053014` / 302329876 | 43556 |
| Experimental return Router | `1.2.5-bseedr8` | `0x12053015` / 302329877 | 43556 native, 65024 wrapper |

Manufacturer is 4417. The wrapper contains Router firmware, not a newer Client.
These short names fit Herdsman's 16-byte Basic swBuildId contract. Historical
rc5/rc6 bytes remain sealed and are not relabeled or rebuilt as new releases.

**PM versus non-PM:** this release targets `b28wrpvx / TS011F-BS-PM`
(`OUTLET_BSEED_PM_TS011F`, Client type 65024) with the HLW8012 meter and its own
GPIO map. The non-PM `o1jzcxou / TS011F-BS` (`OUTLET_BSEED_TS011F`, Client type
65026, Router type 43555) is a different firmware and version line. No new non-PM
artifact is being released here. Shared-source guards/tests are not evidence
that a new non-PM build has passed its own native build and hardware gates.

## Included changes and evidence

- Retains cli8 fast OTA polling/periodic normal-poll verification, cli9 bounded
  PM NVM migration, and cli10 OTA startup ordering/native SDK PM read handlers.
- Integrates the missing `e41f499e` socket pin-map guard and persisted-setting
  sanitization. Exact approved PM GPIO configuration is required; unsafe saved
  configuration falls back in RAM without erasing the suspect record. Invalid
  button timing/modes and relay startup/indicator settings are normalized;
  valid startup policies, including PREVIOUS=255, are preserved.
- Fixes two additional code-proven polling gaps: failed fast-poll setup retries
  after one second, and configure/join callbacks preserve active OTA polling.
  The verifier also repairs rate drift during an OTA. Normal 60-second keepalive
  and five-second retry remain. Host C execution checks these branches; RF/SDK
  runtime behavior still needs the canary.
- Enforces the Basic string limit in firmware compilation and checks the actual
  length-prefixed string in built binaries. rc5's fresh wire response contained
  its new but overlength name; stale cached cli8 was not proof of a missing bump.
- Scopes converter fixed scales to explicit PM board/model/build contracts.
  Legacy v8u4/unknown identities retain scale discovery and do not have their
  cached divisors overwritten by Client constants. Tests execute the generated
  helper against ZHC 26.103.0 / Herdsman 10.9.1, pinned to deployed Z2M 2.14.0.
- Fixes the remaining converter/provisioner reporting mismatch: the normal PM
  configure callback now installs exact raw-unit rules 10/60/5 power,
  5/300/50 current, 5/300/5 voltage and 10/600/1 energy. Previously current
  and voltage defaulted to 10-second minima and voltage change became 500
  raw units (5 V), so the provisioner still needed a repair pass. Tests compare
  the actual library-generated rows against the Python provisioner and require
  no missing rows. Fresh and stale-cache setup tests decode 23000 cV -> 230 V,
  1250 mA -> 1.25 A, 288 W -> 288 W and 12345 Wh -> 12.345 kWh without custom
  calibration. Actual hardware/HA display remains an acceptance check.
- Applies the known PM scale contract before decoding the first report, even
  when configure has not yet run and the cache is empty or contains old Router
  scales. It clones converter callbacks rather than mutating shared ZHC objects;
  legacy/unknown firmware paths retain their own scale semantics.
- Pins Basic date code to the candidate's immutable release date (`20260927`).
  The previous `__DATE__` input made the same source differ on another day;
  same-day repeated builds could not detect that. A C regression compiles the
  date code with two different simulated compiler dates and requires identical
  executable bytes and the correct length-prefixed date value.

Status 139 in the Workroom evidence was Read Reporting Configuration NOT_FOUND,
not Read Attributes UNSUPPORTED_ATTRIBUTE (134). Native PM attribute-read
support remains a hardware acceptance gate, not a disproved fix. See the
[corrected defect record](bseed_pm_client_defects_20260926.md).

## Build/tooling improvements

`bseed_pm_release.py` is the single candidate definition consumed by both build
scripts and verifiers. Native validation checks the real OTA tuple, subelement,
Telink startup marker, embedded version/size/CRC and Basic string, beyond hashes
in manifests. The role matrix performs two clean builds per role and requires
byte equality. `bseed_pm_seal.py` verifies downloaded bundle evidence and seals
all role/return identities without rewriting historical hashes.
Manifest validation uses explicit failures so Python optimization cannot disable
board/source/hash checks. Header-version/flags/stack checks reject malformed OTA
framing. Bundle tests reject missing reproducibility evidence, dirty/wrong-board
manifests, stale source, report/hash mismatch and changed native bytes.

CI adds executed poll failure/rejoin tests, PM/non-PM Router/Client pin-map and
corrupt-setting boots, preservation of all valid startup policies, corrupted
image rejection, seal/idempotency rejection cases and real converter configure
tests. The full existing suite, native toolchain and role-specific gates remain.

Read the updated [build skill reference](../skills/bseed-zigbee-ota/references/build-and-verify.md)
for allocation, CI, artifact retrieval, sealing and future-version steps.

## Limits and next hardware work

This does not establish the original downlink problem's RF root cause, prove
physical metering, or justify a coordinator rebuild. No speculative SDK patch,
parent-table change or network-wide operation is included. The bounded legacy
NVM failure fallback may discard corrupt legacy energy/calibration records;
retention must be measured against a pre-upgrade snapshot.

KitchenSocketLeft is the proposed same-role canary, currently historical cli6
with no load reported by its owner. Use the [acceptance plan](bseed_kitchenleft_client_acceptance_plan_20260926.md):
short pre-upgrade identity/settings/readiness baseline, exact-artifact upgrade,
fresh wire build/query/ZDO role, bidirectional reads and relay/button tests,
metering with a known test load and loaded-to-zero transition, fresh commissioning,
parent recovery, OTA responsiveness and sustained soak. Then pilot 2–3 matching
PM sockets across different parents before considering wider deployment.
Existing cached scale values alone cannot prove empty-cache provisioning.
Keep firmware and converter hashes paired throughout acceptance.

The converter must be installed before the canary. Ordinary Z2M configure
performs binds/reporting/scale setup; the existing authorized OTA campaign
already invokes bounded PM provisioning after fresh post-OTA identification,
so the owner should not need a separate manual repair command. With the correct
converter, that provisioner should audit already-correct rows. Historical
percentage-calibration workarounds require a captured baseline and deliberate
removal when replacing the raw-scale workaround; do not silently erase real
sensor calibration. KitchenLeft's last snapshot had no such calibration options.

## Verified artifacts

Pending successful exact-SHA public CI, download verification and registry sealing.

## Pre-commit logic review, 2026-09-27

Requested by the owner before commit. Reviewed the firmware diff, NVM fallback
and setting paths, polling callback lifecycle, pinned SDK OTA event order, native
packaging and identity rules, actual deployed ZHC configure/conversion code,
campaign provisioning sequence, test fixtures, manifests, CI and sealing.

Findings repaired in the uncommitted candidate:

1. **High: a fresh PM configure still required repair.** Library defaults produced
   different current/voltage report rows from the provisioner. Explicit raw-unit
   thresholds and runtime comparison now cover the complete setup contract.
2. **High: early reports could publish values at the wrong scale.** Correct scales
   only inside configure were insufficient before configure completed. Exact
   board/model/build report callbacks now seed scales before decoding; tests
   include both missing and stale cache and protect legacy firmware behavior.
3. **High for immutable builds: wall-clock build date changed the binary.** The
   release date is now explicit, consumed by both role scripts and recorded in
   manifests; a simulated date change must not alter compiled output.
4. **Medium: optimized Python bypassed manifest checks.** Runtime exceptions
   replace assertions in the artifact gate. Added full-bundle rejection tests
   beyond isolated SHA/registry tests.
5. **Identity regression covered:** an NVM `i43556` override must not make a PM
   Client silently query the Router image type after upgrade; exact pin-map
   validation rejects that modified configuration and uses compiled defaults.

Investigated but did not change: IMAGE_DONE is issued after the Upgrade End
response in [pinned Telink SDK V3.7.2.0](https://github.com/telink-semi/telink_zigbee_sdk/blob/V3.7.2.0/tl_zigbee_sdk/zigbee/ota/ota.c).
Also, `ota_queryStart` checks for an existing OTA timer before installing a
query timer. No evidence from these paths justified another apply-path/SDK patch.

Open acceptance gaps are explicit: native Telink builds and all new tests still
need public CI; real PM reads, physical scaling/zero-load transition, firmware
application, retained energy/settings, fresh commissioning and parent recovery
need hardware. The corrupt-legacy-record fallback can lose legacy energy, and
old manual percentage calibrations could double-correct correct new values;
snapshot/audit these rather than deleting them indiscriminately. No new non-PM
release is included. No source commit, push, live converter write or OTA has
been performed for this candidate as of this review.

## Second review: dispatch, build guards and responsiveness

Additional findings repaired locally before publication:

1. **Build blocker:** persisted relay startup validation referenced nonexistent
   `ZCL_START_UP_ONOFF_TOGGLE`; corrected to the actual
   `ZCL_START_UP_ONOFF_SET_ONOFF_TOGGLE`. Native compilation must still pass CI.
2. **High: plain state GET never reached the endpoint correction.** Deployed
   Z2M 2.14.0 `publish.ts` excludes endpoint-scoped converters when an MQTT GET
   has no endpoint suffix. Added a read-only unscoped state fallback to EP2;
   explicit `state_relay` writes keep their original handler. A real-library,
   generated-definition regression exercises selection before invocation for
   plain GET, scoped GET and availability, for both socket models. This repairs
   a host-side failure that can resemble an unresponsive device; it does not
   establish the cause of independent radio timeouts.
3. **Gate bypass:** the legacy live OTA runner still had assertion-based
   safeguards even after the manifest gate was hardened. It now refuses Python
   optimized mode before imports/argument handling. A subprocess regression
   checks that `-O` cannot start it.
4. **Test blind spot:** the new Client corrupt-settings fixture selected a
   generic EndDevice, not the mains Client branch. It now compiles the actual
   mains Client defines for both boards; corrected hexadecimal output casing.
5. **Build invariant:** mains Client role/Rx-on/no-sleep were configured correctly
   but contradictory flags could silently defeat them. The effective stack
   configuration now rejects a mains Client with sleep enabled, receiver off,
   or Router role. Preprocessor cases cover valid and contradictory inputs.

The PM read probe now permits both power scale attributes as well as voltage,
current and energy scales; shared source checks cover all eight scale attributes.
These checks validate declared attributes, not on-air SDK behavior. The pinned
ZHC `onOff({endpointNames: [...]})` already clones its converter, so no global
converter mutation defect was found in that path.

The actual runtime contract remains: receiver continuously enabled, MCU power
management disabled, 60-second parent MAC keepalive, 250 ms polling during OTA,
normal setup retry after 5 seconds, and fast-mode verification/retry each second.
Normal mode is verified each minute. These are configured/checked intervals,
not measured guarantees of parent retention or packet delivery. Power-management
`PM_ENABLE` is unrelated to the socket's power-metering PM product designation.

No fresh CI/native builds or hardware tests have run for these changes. The
review does not claim missing reads or long-term downlink loss are proven fixed.
