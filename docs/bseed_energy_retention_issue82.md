# PM cumulative energy persistence — issue 82

Issue [#82](https://github.com/analienx/tuya-zigbee-switch/issues/82) records a
legacy PM Router to r13 update whose raw delivered-energy total regressed from
128 Wh to zero, then began accumulating again. The private campaign remains
unverified. A cached Home Assistant value or OTA transport success cannot
establish energy retention.

## Source defects and change

Two executable persistence paths can turn existing energy into a zero baseline:

- Telink SDK 3.7.2.0 `nv_flashReadNew(single=1)` invalidates an item on a
  checksum failure. A subsequent read reports absence, allowing metering to
  overwrite it. Application HAL reads now locate the item without mutation,
  validate the fixed record size and bounds, and read/check it by index. An
  existing but unreadable record stays an error across retries. The SDK also
  accepts size multiples and copies the stored length; HAL rejects mismatched
  lengths before reading into the application buffer.
- Bounded legacy PM migration preserves old records when it cannot finish, but
  previously skipped energy restoration after the retry cap. Metering now
  falls back to the historical endpoint-1 record only after the raw PM identity
  classifier allows it and the unified record is truly absent. Its next
  checkpoint persists the restored total plus subsequent accumulation. An
  unreadable or invalid legacy record leaves the baseline unknown and blocks
  saves; a valid unified record, including an explicit zero reset, always wins.

Periodic energy writes now require matching readback before advancing their
successful-persistence state. Existing controlled OTA/reboot handlers still
defer reboot until the checkpoint is verified. Reserved UINT48 invalid totals
are rejected at restore and migration.

These are reproducible source defects, not proof of which flash condition
occurred on the production socket: no raw flash dump or firmware serial trace
was captured for that update. Firmware cannot reconstruct an already lost
total from the MQTT baseline. New code also cannot add a checkpoint to the old
firmware while it applies its first upgrade. Abrupt power loss still has the
ordinary five-minute checkpoint window.

## Candidate identities and verification

Fresh identities preserve every prior sealed artifact:

| Board | Router | Client | Native file version | Build date |
| --- | --- | --- | --- | --- |
| TS011F-BS-PM | `1.2.5-bseedr14` / 43556 | `1.2.5-bseedcli17` / 65024 | `0x1205301c` | `20261013` |
| TS011F-BS | `1.1.3-bseedr13` / 43555 | `1.1.3-bseedc11` / 65026 | `0x11023018` | `20261013` |

The shared application HAL affects both boards, so all four identities move.
The pinned converter recognizes the new PM build IDs. Candidates remain
unsealed and hardware-unaccepted until their separate release gates pass.

`test_telink_nvm_restore.py` executes the pinned SDK's real read functions with
fault-injected storage. It checks repeated checksum faults without deletion,
recovery, size/header disagreement, bounds, and true absence.
`test_bseed_energy_ota_retention.py` executes boot classification and metering
for both PM roles: capped migration, accumulation, failed checkpoint, reboot
restoration, readback mismatch, explicit reset, corrupt/absent legacy data and
foreign identity. The PM role matrix includes both tests and the existing
energy-restore and controlled-reboot regressions.

Required acceptance evidence is GitHub-hosted exact-candidate `test`, PM role
matrix, non-PM role matrix and paired Router TC32 validation. Green offline
evidence does not confirm retained energy on hardware. A future authorized
canary must independently compare fresh raw energy before/after OTA and a
controlled reboot, alongside the existing device-specific hardware gates.
