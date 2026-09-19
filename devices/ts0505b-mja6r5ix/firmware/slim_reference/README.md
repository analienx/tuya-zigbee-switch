**ARCHIVED PLATFORM MISMATCH:** The module identified for the three RGB+CCT bulbs is ZTU/Telink, not EFR32MG21. This Silicon Labs reference can only be used for historical comparison, never as a ZTU firmware/OTA payload.

# Slim TS0505B reference build (experimental; do not flash)

This project uses Silicon Labs Simplicity SDK **2026.6.1** to build a minimal
EFR32MG21 Zigbee 3.0 Router / Extended Color Light reference application.
It preserves the generic RGB+CCT state/ZCL adapter but intentionally leaves
the physical RGB+CCT output hook inert until the production board is proven.
It does **not** contain an OEM stock image or bootloader configuration.

The project template removes optional Green Power, ZLL, CLI and diagnostic
components to keep the full-image OTA smaller than the observed pre-byte
acceptance ceiling. Its Application Properties version and outer OTA file
version are both `0x10003608`; matching those two numbers is not proof of
stock bootloader signature, rollback or product-ID acceptance.

## Offline build

Run from the repository root with Silicon Labs CLI and tool paths installed:

```bash
python -m helper_scripts.ts0505b.build_slim \
  --slc /path/to/slc --sdk /path/to/simplicity_sdk.slcs \
  --cmake /path/to/cmake --commander /path/to/commander \
  --ninja-dir /path/to/ninja-directory \
  --chip-kib 768 --out /new/empty/build-output
```
Change `--chip-kib` to `1024` only when building the alternative SDK target;
**do not choose a release target until the installed silicon has been identified**.
The builder requires a new or empty output directory and never connects to
Zigbee2MQTT or the bulb. It creates a BIN, GBL and outer Zigbee OTA file, checks
the two file formats, verifies application version and flash-program ranges,
and produces `build_result.json` with SHA-256 hashes and a fail-closed status.

## Verified local development results

- `EFR32MG21A020F768IM32`: application 183,088 bytes; OTA 183,234 bytes.
- `EFR32MG21A020F1024IM32`: application 183,088 bytes; OTA 183,234 bytes.
- The variants produce different BIN/GBL/OTA hashes; they are not interchangeable.
- A one-device metadata-only offer of 183,234 bytes produced a block-0 request;
  the probe aborted at block 0, transmitting zero candidate bytes.

## Remaining release gates

- Confirm the *installed* silicon flash density, actual memory map, stock GBL
  acceptance policy and rollback restrictions.
- Verify OEM board pins, output polarity, PWM frequency and safe startup;
  this reference currently does not drive the actual LEDs.
- Establish a recoverable stock backup / physical recovery process before
  permitting an irreversible first application transfer.
- Prove actual download, integrity check, reboot, network routing and RGB+CCT
  behavior on an isolated recoverable test unit.

A successful build or Query Next Image response must **never** flip the
repository's deployment-ready flags or authorize a first flash.
