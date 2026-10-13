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
The pinned converter recognizes the new PM build IDs. The four reviewed
candidates are sealed in the identity registry from the runner-produced
proposal below. Hardware acceptance remains pending.

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
policy remain intact. Both findings are addressed, with no remaining
merge-blocking observations in this author review. This does not establish
hardware acceptance.

## Sealed reviewed candidate evidence

Firmware source: `04445f4e7915f26d648832ca1d6215a48f34f0a9`.
All verification ran on GitHub-hosted runners from this exact clean SHA:

- [Full tests and lint](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38355922881): 1,104 passed.
- [PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38355922685): Router and Client built and independently reproduced.
- [Non-PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38355922832): Router and Client built and independently reproduced.
- [Paired Router TC32 gate](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38355922872): both boards and aggregation passed.
- [Candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/38355922848): authenticated prerequisites, native integrity and atomic four-tuple seal proposal passed.

The committed seals copy that workflow's reviewed registry proposal. No local
build, test, lint, sealing or repository verification was executed. Recheck
the registry commit on public CI before merging: its rebuilds must match these
same sealed bytes. Package artifact name:
`bseed-flash-candidates-04445f4e7915f26d648832ca1d6215a48f34f0a9`.
The matrix files are each role's `forward.ota`; the package renames them as
listed below and records native `.bin` hashes in `CANDIDATE_PACKAGE.json`.

| Package OTA filename | SHA-256 | SHA-512 |
| --- | --- | --- |
| `pm-router/firmware.ota` | `3617a4d732da99a89ceae10e4058c6b903c5fe5455dbbd0a2aa5467c8f1c22dc` | `ee53608b0afcdcbed08932d18c0286a8ecb82dcdce7a362feb33e4c7317035633460032abe1bf49d77c761d22f18279c7f231b22c61fb388a80223f6b77c9603` |
| `pm-client/firmware.ota` | `0bb1fd9de5ae2ed9a1ad56bde439ccc5ed5249b95a8506b433501a21f46b1033` | `7be6201f19c00f5432f561919966948068c8c457a6d334cab28703905b6c9778840e702a33c3bd895e80d14b128fb1dee9b2134b14c2f56f3eafa6a6482358ca` |
| `nonpm-router/firmware.ota` | `0e0fb734260620dc716485f86d95c8adf9e4705666ebb47325a407610584928d` | `078f68e9a3cdebadc2b89c2a029f201ee4402e7ce5459ef51f95887c70bf60872fa9b36748d72d57607b80c917ddb9251e20750089452d2aa0bd66b2a4a9f675` |
| `nonpm-client/firmware.ota` | `ebae5432dc18b0b35d0fc1d8c143762ebb6dc29013611d5e1d227623dcb8c9a7` | `093aa04c55c06857dc6f4b7f81d05e84f46103915cefdd8746019e0d150d68ee29abbd1b9ab0d4da48f7a35ed159d4d34e7516c5d273beb3db1a4a0754e45dc8` |

No device was flashed, reset or reconfigured during this review. The first
deployment remains a target-specific canary with current network/campaign
eligibility, actual boot identity and fresh before/after energy evidence.
