# BSEED private FORCE role-transition transport

BSEED release identities remain strictly monotonic. `0xFFFFFFFF` is not a
firmware release number and must never be used to relabel or publish a normal
candidate. It is available only as a private OTA transport wrapper for deliberate
Router/Client hardware testing on an already-custom BSEED socket.

This follows the useful part of Romasku's FORCE mechanism while narrowing its
scope. The installed payload is still the exact sealed native candidate. Only
the outer Zigbee OTA image type and file version are changed so the currently
installed role will accept an equal-version cross-role image.

## Invariants

A FORCE test transition is accepted only when all of these are true:

- board is `b28wrpvx / TS011F-BS-PM` or `o1jzcxou / TS011F-BS`;
- both source and destination are already-custom BSEED firmware;
- source and destination Zigbee roles are different;
- the native destination image already exists in the checked-in sealed identity
  registry with its exact build string and SHA-512;
- the private profile pins the native image and its SHA-256;
- bytes after the 56-byte Zigbee OTA header are identical to the sealed native
  image;
- the wrapper changes only the outer image type to the source role's query type
  and the outer file version to `0xFFFFFFFF`;
- the wrapper, one-entry index, profile and evidence remain outside git;
- the normal campaign exact-IEEE confirmation, shared-network lock, preflight,
  check, scoped rejoin, metadata and postflash gates remain in force;
- a failed or incomplete transfer is never retried automatically.

`helper_scripts/bseed_force_test_wrapper.py` creates the wrapper. It refuses
output inside the repository and refuses an unsealed destination payload.

Example:

```bash
python helper_scripts/bseed_force_test_wrapper.py \
  --native /private/ota/native-router.ota \
  --output /private/ota/force-client-to-router.ota \
  --board b28wrpvx \
  --source-role EndDevice \
  --target-role Router \
  --target-build 1.2.5-bseedr9
```

The resulting private campaign sets `force_test_transition: true`,
`native_image` and `native_sha256`. Its served OTA tuple uses the
source role's image type and `file_version: 0xffffffff`. The expected
postflash role/build always describe the native payload, not the wrapper.

## Acceptance matrix

The intended same-hardware A/B/A sequence is:

| Fixture | Step 1 | Step 2 | Step 3 |
| --- | --- | --- | --- |
| KitchenSocketLeft (PM) | Client `cli12 / 0x12053016` | FORCE exact Router `r9 / 0x12053016` | FORCE exact Client `cli12 / 0x12053016` |
| BedroomSocketCabinetRight (non-PM) | Client `c7 / 0x11023014` | FORCE exact Router `r10 / 0x11023014` | FORCE exact Client `c7 / 0x11023014` |

The first step uses normal monotonic OTA when the installed candidate is older.
FORCE is used only where equal-version cross-role testing would otherwise no-op.

The PM `1.2.5-bseedr10 / 0x12053017` version-bump return experiment is retired.
Its already-public native/wrapper tuples remain registry tombstones only, so those
byte identities cannot be reused; there is no current build, seal, CI artifact or
deployment path for it. PM role-interchange acceptance uses FORCE around the exact
sealed `r9`/`cli12` candidates instead.

## What FORCE does not prove

A successful transfer proves neither boot nor role acceptance. After each role
transition require the same IEEE to rejoin through the bounded scoped window,
fresh ZDO role and Zigbee2MQTT metadata agreement, exact Basic `swBuildId`,
relay behavior, settings/energy retention, PM reporting where applicable and the
role-specific hardware gates. Router acceptance additionally requires routing
and child-parenting evidence; Client acceptance requires receiver-on parent-loss
and rejoin behavior. PM release acceptance still requires a controlled known-load
to zero test and soak.

The non-PM recovery gate is intentionally unchanged. Creating a FORCE wrapper
does not grant a new no-disassembly risk waiver for BedroomSocketCabinetRight;
a non-PM role transition still needs the separately authorized recovery/risk
requirements before the campaign runner will write hardware.
