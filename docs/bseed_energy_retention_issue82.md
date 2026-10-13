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
  lengths before reading into the application buffer. HAL validates complete
  payload checksums plus modern index CRCs and bypasses the SDK's global length
  table so another module's item-ID registration cannot silently shorten a read.
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
| TS011F-BS-PM | `1.2.5-bseedr15` / 43556 | `1.2.5-bseedcli18` / 65024 | `0x1205301d` | `20261013` |
| TS011F-BS | `1.1.3-bseedr14` / 43555 | `1.1.3-bseedc12` / 65026 | `0x11023019` | `20261013` |

The initial PM r14/cli17 and non-PM r13/c11 tuples are retired before
deployment. Their immutable hashes/provenance come from the GitHub-hosted
seal proposal for `3f12db580afcb68f2a85ee54e987be1f51edbd91`; review changed
firmware bytes, so all four reviewed candidates have fresh identities.

The shared application HAL affects both boards, so all four identities move.
The pinned converter recognizes the new PM build IDs. Candidates remain
unsealed and hardware-unaccepted until their separate release gates pass.

`test_telink_nvm_restore.py` executes the pinned SDK's real read functions with
fault-injected storage. It checks repeated checksum faults without deletion,
recovery, full-length restoration despite SDK table truncation, modern index
and payload CRCs, size/header disagreement, bounds, and true absence.
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

## Author code review before merge

Reviewed the complete PR diff, the pinned Telink record writer/reader format,
HAL callers, migration fallback, explicit-reset precedence, checkpoint retry
state, both role builds, converter recognition and identity allocation.

Two additional findings required fixes before merging:

1. `nv_flashReadByIndex` uses a global length table keyed only by numeric item
   ID. It can return success after copying only a prefix of an application
   record, even though it verifies the full payload. HAL now copies/checks the
   full fixed-size record itself. A real SDK reference read in the regression
   demonstrates the short-copy behavior before checking HAL's complete value.
2. SDK reads verify the payload CRC but ignore the index CRC in the upper half
   of the modern header checksum. HAL now checks both CRCs, plus index identity,
   state and sector/index bounds. The regression proves the SDK accepts a bad
   index CRC while HAL rejects it without deleting anything.

The existing flash/NVM layout, stored record ABI, role-specific GPIO and reset
policy remain intact. Final acceptance requires green exact-SHA CI and committed
runner-produced seals; this author review does not establish hardware acceptance.
