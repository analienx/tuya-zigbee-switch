# PM Client to Router: package and apply-path evidence

The additional offline candidate is Router `1.2.5-bseedv8u5-rc6`, version
`0x12053013` (302329875). It contains rc5's source changes with a new compiled
version/build identity, not a new boot/apply fix. The PM workflow builds both
rc5/cli10 and this extra Router from one clean source commit on public runners.

The separate `bseed-pm-client-return-experimental-SHA` artifact contains:

- `from-client.ota`: outer manufacturer 4417, image type 65024; Router payload.
- `forward.ota`: manufacturer 4417, Router type 43556; identical payload.
- `manifest.json`: provenance, configuration, both headers and file hashes.
- `CLIENT_RETURN.json`: payload/CRC/version checks and explicit false values for
  `applyPathFix`, `hardwareAcceptance`, and `deploymentReady`.

The builder validates native startup markers, firmware length, embedded version,
CRC, matching Router payload and both OTA identities. Only image-type bytes 12/13
may differ between the two files. This does not validate flash contents on a device.
No fleet index is modified. Preserve the failed campaign's evidence and lock.

## Corrections to the proposed cli8 -> cli10 -> rc5 plan

1. The Router build script already *generates* `from-client.ota`; the rc5 matrix
   upload intentionally omitted it. No downloaded usable rc5 return package was
   supplied. Building a new package in CI is possible; this is not an inherent
   inability to make one.
2. cli10 and rc5 both use `0x12053012`. A rc5 wrapper has the same OTA tuple as
   cli10 but different bytes, and a running cli10 ignores an equal version.
   Therefore the return target must have a higher native AND outer version and a
   distinct build identity. rc6 uses `0x12053013`, allocated through
   `bseed_ota_identity.py suggest-next/emit-make-vars` after sealing rc5/cli10.
   Reserve type 65024/version 0x12053013 for this Router wrapper, never cli11.
3. `e83cb751` proves the rc4 attempt left the device on cli8 after recovery. It
   does not isolate a general wrapper incompatibility or the exact failed stage.
   Do not repeat it blindly, but do not label all cross-role OTA impossible.
4. cli8 performs the receive/validate/apply operations for cli8 -> cli10. That
   update cannot use fixes inside cli10 before cli10 boots. A successful boot
   proves one same-role update under cli8. It does not prove cli10's outgoing
   updater or a role transition. cli10 fixes query startup; its successful OTA
   completion callback still calls the existing `ota_mcuReboot()`.
5. A same-network update does not exercise a *fresh-join* initialization bug.
   Validate fresh commissioning and empty-cache converter provisioning separately
   on a spare. Keep production calibrations until actual scaling is checked;
   installing the forced-scale converter while retaining raw-value compensation
   can double-correct measurements.
6. Reading relay ON is not proof of load safety or of uninterrupted power during
   reboot. The boot and role-reset paths also need physical retention checks.

## What the pinned SDK actually does

Source: [Telink V3.7.2.0 ota.c](https://github.com/telink-semi/telink_zigbee_sdk/blob/V3.7.2.0/tl_zigbee_sdk/zigbee/ota/ota.c).
`ota_queryNextImageRspHandler` ignores equal file versions. The upgrade-end
response is checked against the running preamble's manufacturer/type and the
downloaded version, then moves to countdown or waiting-to-upgrade.
`ota_upgrade` has retry/countdown paths into `OTA_EVT_COMPLETE`.
`ota_mcuReboot` checks the staged native image's size, startup marker and CRC,
writes boot-selection flags and resets only on success; it can return silently
if native validation or the checked flag write fails. These functions contain
no Client-versus-Router role check. The role transition runs after the new image
boots. This source review does not prove which branch cli8 executed on hardware.

## Next evidence and practical sequence

- Preserve and inspect the failed rc4 transaction: actual device Upgrade End
  status, transmitted response tuple/currentTime/upgradeTime, delivery/retries,
  announce, and fresh running Basic build plus OTA query tuple. A server OK
  response alone cannot distinguish missing response, callback/timer failure,
  staged-flash validation, flag-write failure or unsuccessful new-image boot.
- cli10 remains a reasonable separately authorized same-role diagnostic canary
  after exact-target eligibility and failed-lock reconciliation. Confirm its
  actual build and query version, relay/settings/energy retention and direct PM
  reads. Do not call this proof of repaired application of future updates.
- Before a cli10 -> rc6 experiment, obtain evidence from a spare for the apply
  stage, or diagnose and fix an identified fault in a *new* Client build first.
  If diagnostic instrumentation changes Client code, assign a fresh identity;
  cli10's sealed bytes must remain immutable. Do not bypass SDK image validation
  or force boot flags as a speculative repair.
- A later authorized return experiment uses the existing profile-driven
  `transition` runner, exact source and destination identities, scoped rejoin
  and fresh role/build verification. The rc6 package by itself is not permission
  to clear locks or flash. Direct-wire Router installation remains a separate
  physical recovery option when its board-specific recovery route is verified.

The source/package work can be completed now; the rc4 apply root cause remains
unproven and no live hardware acceptance is claimed.
