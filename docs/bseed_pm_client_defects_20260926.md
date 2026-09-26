# PM investigation: reporting, OTA startup, provisioning and build identity

## Latest correction: 2026-09-26 19:00 Europe/Prague

WorkroomSocketCabinet is now a **Router running rc5**. Read-only inspection of
the existing HA logs confirmed its fresh node descriptor, the actual Basic
read response and spontaneous OTA query. Two conclusions below were incorrect:

- At 18:46:17, EP1 Basic Read Attributes response, transaction 6, attribute
  `0x4000`, status 0, contains **`1.2.5-bseedv8u5-rc5`**. Immediately afterwards
  Herdsman logs `Ignoring attribute swBuildId from response` because the maximum
  length is 16. The 19-byte firmware string is correct for the built image but
  violates that limit. `cli8` is the retained database value, not the wire reply.
  The downloaded, sealed rc5 artifact embeds rc5 and contains no cli8 string.
- At 18:45:46 the 139 results came from `device/reporting/read`, which invokes
  `endpoint.readReportingConfig()`. These are Read Reporting Configuration
  results (`0x8B`, NOT_FOUND), **not Read Attributes results** (`readRsp`). They
  do not prove measurement values or divisors are unreadable. Unsupported
  Attribute is `0x86` (134). Missing reporting entries, including entries that
  need not exist for fixed scale attributes, are a different question.
- At 18:57:36 the device's own OTA query reports manufacturer 4417, image type
  43556 and version 302329874. Together with the Router node descriptor and
  actual rc5 wire string this corroborates the new running image.

The historical observations below are retained with these corrections. Do not
write another speculative PM read-path patch on the strength of status 139.
The earlier optional SDK-cluster-handler change is still hardware-unverified;
these reporting-configuration probes prove neither its success nor its failure.

### Required next actions

1. Keep rc5/rc6 hashes immutable. A metadata repair needs a **new version** and
   a build ID of at most 16 ASCII bytes (for example `1.2.5-bseedr7`, subject to
   allocation). `emit-make-vars` now rejects overlength/non-ASCII new IDs.
   This guard prevents new allocation mistakes; it does not modify running rc5.
2. rc6 has the same 19-byte naming defect. It is not a suitable next deployment
   candidate. Do not flash it merely to remedy the stale cached string.
3. Use genuine endpoint-1 ZCL **Read Attributes**, with transaction-matched raw
   `readRsp` evidence, for measurement values and scale attributes. Verify that
   the request is on EP1, then separately inspect reporting configuration.
   Do not interpret an MQTT value or reporting-config reply as an attribute read.
4. A scale read may populate Z2M's scale cache and change conversion. Coordinate
   that test with a snapshot of the current scaling/calibration state and
   removal of temporary compensation only when effective conversion is verified;
   otherwise voltage/current/energy can become double-corrected. Do not remove
   calibrations merely because firmware was offered or an interview succeeded.
5. Retain the campaign's unverified state until the metadata contract, reporting,
   scaling and retention checks pass. Relay OFF is the reported post-reboot state;
   this investigation sent no relay commands, reads, configuration or OTA requests.

Evidence was inspected read-only at 18:58–19:00 Europe/Prague. Full live logs and
database remain private on HA. Protocol references: Zigbee2MQTT
`lib/extension/bridge.ts::deviceReportingRead`, Herdsman
`src/zspec/zcl/definition/status.ts`, and firmware `src/zigbee/basic_cluster.c`.

## Historical hypotheses before raw-frame verification

Fleet-proven tonight on `WorkroomSocketCabinet` (TS011F-BS-PM `b28wrpvx`, client `1.2.5-bseedcli8`)
plus sibling clients (`cli6`) and a `v8u4` router. Router image assumed okayish; the **client
flash image is the fix vehicle**.

## Defect 1 — EP1 0x0B04/0x0702 attributes unreadable (client: all, router: divisors)

Live `reporting/read` results (Zigbee2MQTT bridge requests, per-attribute status):

- cli8 client: `measurementType`, `rmsVoltage`, `rmsCurrent`, `activePower`,
  `acVoltageMultiplier/Divisor`, `acCurrentMultiplier/Divisor` → **all status 139**.
- cli6 clients: divisors also 139 (their correct display rests on **stale divisor cache**).
- v8u4 router: measurement values read OK (status 0), divisors 139.

Meanwhile device-pushed attribute **reports work everywhere** (power/voltage/current stream
once reporting is configured). The initial interpretation was a broken ZCL read path;
the reporting/configuration distinction above invalidates that inference from status 139.
Source registers attributes in
(`src/zigbee/electrical_measurement_cluster.c` `SETUP_ATTR` 0–26, correct 0x0600–0x0605 IDs
in `src/zigbee/consts.h`; cli8 was built from this source). Prime suspect: the HAL
registration in `src/telink/hal/zigbee_zcl.c` (`register_pm_electrical_attrs`, registered
"without linking optional Telink command implementations") and how the SDK serves reads
from it. Consequences: Z2M shows raw values (voltage 100x), `electricityMeter` setup reads
fail, fresh joins stay unmetered.

## Defect 2 — client OTA query loop dead until power-cycle

cli8 sent **zero** spontaneous `queryNextImageRequest` in 4.5 h after a fresh join and ignored
two server `imageNotify` (payloadType 0) checks. Only after an owner power-cycle did it query
and the OTA check pass (transfer then flowed normally). Entry points:
`ota_queryStart(OTA_QUERY_INTERVAL)` at commissioning/init in
`src/telink/hal/zigbee_network.c`, event handling in `src/telink/hal/zigbee_ota.c`,
rx-on EndDevice poll/keepalive interaction. A client that cannot query cannot be updated
in the field.

## Defect 3 — fresh joins never self-provision PM reporting

On a fresh join Z2M configures EP2 fully but writes **zero** EP1 binds/reportings
(`database.db`: EP1 `binds: []`, `configuredReportings: []`; siblings carry
`binds [2820,1794]` + 4 reportings). Hypothesis: the converter's `electricityMeter`
extend aborts EP1 setup because its divisor reads fail (see Defect 1). Fix belongs in the
converter generator (`helper_scripts/templates/switch_custom.js.jinja` +
`helper_scripts/make_z2m_custom_converters.py`) with a test asserting EP1 binds/reporting
after configure.

## How to work (constraints)

- **Public repo: use public GitHub runners for all firmware builds.** No local or WSL
  toolchain builds as release artifacts. Push a branch, let CI build, verify the artifact
  manifest (`sourceCommit`, clean tree, board `OUTLET_BSEED_PM_TS011F`, OTA header
  manufacturer 4417 / type / file version, sha256) before staging anything.
- Version automation is mandatory: `helper_scripts/bseed_ota_identity.py`
  suggest-next/emit-make-vars flow. Never reuse a (image_type, file_version) identity.
- A work-in-progress test addition exists uncommitted in the working tree
  (`tests/test_pm_cluster_layout_guard.py`, +75 lines: scaling-attribute registration
  + stub read-back). Review it, keep or rework it; its runtime half needs a Linux runner
  (stub binary does not execute on Windows).

## Success criteria (all must hold on a spare before any fleet flash)

1. Fresh-joined PM client answers direct reads of `rmsVoltage`, `activePower`,
   all six 0x0B04 scaling attributes and metering `multiplier`/`divisor` with status 0
   and fleet-proven values (1/100, 1/1000, 1/1, metering 1/1000).
2. Z2M displays natively scaled PM with no calibration options and no reliance on cache.
3. Fresh join yields EP1 binds `[2820, 1794]` + the 4 reportings with zero hand-provisioning.
4. Fresh-joined client emits spontaneous OTA queries on the configured cadence AND answers
   a server `imageNotify` inside Zigbee2MQTT's 60 s check window.
5. Client + router role matrix builds green on runners with the standard pytest suite.

## Operation log 2026-09-26 — WorkroomSocketCabinet cli8 → rc4 router return

- Staged `from-client.ota` (sha256 `675b06ab…`, type 65024 / 0x12053011) + private
  one-entry index on the HA image server (`:8899`); container-fetch verified byte-exact.
  Campaign `prepare` + `preflight` green (relay OFF, power 0 W).
- Preflash findings/fixes: correct relay key is `state_relay` (not `state`); EP1 PM
  reporting was never configured — applied the provisioner's bounded 4-write setup and
  PM streams since; temporary Z2M percentual calibrations (`voltage -99`,
  `current/energy -99.9`) compensate the raw stream — **revert once scaled reads work**.
- OTA check failed 2x (device never queried in 4.5 h); passed first try after owner
  power-cycle (Defect 2 live).
- Transition flash: 100% (~34 min, 32 B blocks), `upgradeEnd` OK 16:55:30, **no
  post-upgrade announce**. Scoped rejoin unconfirmed x2; inventory stale
  (EndDevice/cli8/FAILED); targeted interview to follow.
- Campaign lock: `ota_transfer_ok_postflash_unverified`. Private workdir (outside git):
  `bseed-ota-workroom/campaign-workroom-return`.

## Outcome 2026-09-26 ~17:45 — cross-role OTA TRANSFERRED but NOT APPLIED

- After owner power-cycle + re-pair, fresh interview proves the socket still runs
  **`1.2.5-bseedcli8` EndDevice** (new NWK 14131, interview SUCCESSFUL). No router.
  Z2M `update.installed_version 302329873` is transfer bookkeeping, not running firmware.
- Reconstruction: `upgradeEnd` OK 16:55:30 but the device never rebooted into the new
  image (no announce in 30 min; interim interviews failed). Owner power-cycle cleared
  what looks like a hung OTA state machine; the Telink bootloader never swapped, or the
  reboot never happened. This client-header/router-payload (`from-client.ota`)
  attempt failed to establish the new running image. The exact stage and cause
  remain unknown; this does not prove an inherent wrapper incompatibility.
- Relay is ON (owner toggled during testing; preflash baseline OFF untouched by us).
  Calibrations and EP1 reporting stay: still a raw-stream cli8, still needed.
- Image server stopped; Z2M config untouched; join windows closed.

## Outcome 2026-09-26 ~18:50 — ROUTER ACHIEVED via native rc5, with caveats

- The staged rc4 wrapper taught us the device queries OTA as (4417, **43556**, 873)
  after a transfer. Native rc5 (43556, 874) offered as a same-query-type upgrade:
  check passed, transfer 100%, device rebooted and came back as **Router**
  (fresh node descriptor + full re-interview 18:46, all genBasic read live).
- Relay is **OFF** after the reboot (power-on behavior `off`); the connected load is off.
- **Defect 4: invalid-length swBuildId.** Database metadata remains `cli8`, but
  the actual wire reply is rc5. Herdsman discards the 19-byte string (limit 16).
  Campaign metadata correctly refuses the stale cached build.
- **PM read-path outcome remains unverified.** The EP1 status-139 probes were
  reporting-configuration reads. They cannot establish whether measurement or
  divisor attribute reads work. Keep calibrations pending a coordinated scale test.
- Campaign lock remains `ota_transfer_ok_postflash_unverified` (truthful). Image server
  stopped again; MQTT standard.
- Do not repeat the failed wrapper attempt or offer cli10 to this now-Router
  socket. The former cli8-to-cli10 diagnostic proposal is superseded for this
  device. Remaining identity/protocol issues and rc6 limitations are recorded in
  [Client-to-Router plan](bseed_pm_client_to_router_20260926.md).

## Operational context (do not "fix" these in firmware)

- `WorkroomSocketCabinet` currently carries temporary Z2M percentual calibrations
  (`voltage_calibration: -99`, `current/end: -99.9`) compensating the raw stream; they must be
  removed when effective scaling is verified and coordinated with the scale cache.
  Status 139 from reporting/configuration is not evidence about divisors. The
  socket now runs rc5 Router; its cached Basic build still says cli8 due to the
  overlength rc5 string.
