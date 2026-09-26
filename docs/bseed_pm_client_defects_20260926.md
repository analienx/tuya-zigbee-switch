# PM client defects: unreadable EP1 measurement cluster, dead OTA query loop, unmetered fresh joins

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
once reporting is configured). So the attribute tables are fine — the **ZCL read path** for
HAL-registered PM clusters is broken. Source already registers everything correctly
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
  reboot never happened. The client-header/router-payload wrapper (`from-client.ota`)
  strategy fails at the apply step — transfer success does not imply role change.
- Relay is ON (owner toggled during testing; preflash baseline OFF untouched by us).
  Calibrations and EP1 reporting stay: still a raw-stream cli8, still needed.
- Image server stopped; Z2M config untouched; join windows closed.

## Operational context (do not "fix" these in firmware)

- `WorkroomSocketCabinet` currently carries temporary Z2M percentual calibrations
  (`voltage_calibration: -99`, `current/end: -99.9`) compensating the raw stream; they must be
  **removed if/when** scaled reads work, and **kept** if the first router image still 139s
  divisors. It is mid-transition to the rc4 router as of 2026-09-26 evening.
