# Tongou TO-Q-SY2-163JZT: firmware acceptance and hardware evidence

Status: **RESEARCH ONLY — NOT FLASH-APPROVED.** Revision: 2026-09-19.
Target: `_TZ3000_cayepv1a` / `TS011F`, ZSU / EFR32MG21 (exact board revision unverified).

## What Gledopto taught us — and the difference

The Gledopto port used a hash-identified stock image, header/flash-map forensic analysis, ISA disassembly, peripheral-protocol tracing, and a reproducible candidate with independent checks. Its candidate was NOT thereby proven safe on a live unit. Apply that evidence hierarchy here, but do **not** transpose Telink B85 CRC, bank layout, pins, or OTA acceptance rules to a Silicon Labs Gecko bootloader.

**New source-backed distinction:** Tuya's official ZSU product kits use `manufacturer=0x1002`, `imageType=0x1602` as standard platform OTA metadata. These values are shared by unrelated Tuya products. `_TZ3000_` is a capability prefix followed by an eight-character product ID (PID); `cayepv1a` is therefore the product-ID *candidate* to use when seeking the matching stock image. The saved Zigbee2MQTT query on stock sample A reports `0x1002/0x1602`, version `75`. None of these values uniquely identifies a stock binary, its cryptographic requirements, or the Tongou board revision.

Tuya references: https://developer.tuya.com/en/docs/iot-device-dev/tuyaso_zigbee_switch_product_development_kit?id=Kd6dpnm52ww2k and https://developer.tuya.com/en/docs/iot-device-dev/tuyaos_zigbee_light_product_development_kit?id=Kd6efghkquo9d .

## Current candidate is not an exact-target production image

The existing native build was compiled and linked with the MG21 1 MiB part selected in its generated project; `commander util appinfo` reports **unsigned** application properties, application version `1`, no certificate and no product ID. It did not establish target-specific configuration, a native temperature driver, a proven relay map, a stock-compatible OTA payload, or an installed bootloader's acceptance policy. The source Makefile still defaults to a different device configuration and an MG21 768 KiB variant unless explicit settings are supplied. A successful `.gbl` generation establishes file syntax, not stock bootability.

## Separate OTA acceptance layers

1. **Zigbee OTA discovery:** outer header has an appropriate identifier/version. The `0x1002/0x1602` query only covers this layer; it is not a signature or board match.
2. **Transport/container:** validate the Zigbee OTA file header, total image length, subelements, embedded GBL format, application properties, application/bootloader version and flash ranges. Do not assume a Telink payload layout.
3. **Stock bootloader:** determine on the EXACT board whether unsigned GBL is accepted, whether signed/encrypted GBL or secure boot are mandatory, whether hardware rollback protection exists, and whether a verified read-back/recovery path survives a failed update. Matching outer headers cannot defeat asymmetric signature verification.
4. **Electrical operation:** even if the image boots, the breaker is not suitable for mains service until the protective functions and physical contact feedback are understood and independently validated.

Silicon Labs documents optional ECDSA-P256 signed upgrades, AES-CTR encrypted GBL, secure boot, and MG21 debug-lock/erase behavior. These are alternative firmware configurations, NOT proof that this Tongou has any particular configuration. See https://docs.silabs.com/shared-content/1.0.5/bootloader-user-guide-gsdk-4/09-gecko-bootloader-security-features .

## Obtain the *exact* stock payload without disturbing installed breakers

Use PID candidate `cayepv1a`, product `TO-Q-SY2-163JZT`, Zigbee manufacturer/model, reported firmware version `75`, and a readable module/board-revision marking when requesting a matching original OTA/update from Tongou or an authorized Tuya developer account. Request the **original signed `.ota` or GBL**, matching hardware revision, full release notes, bootloader/flash/rollback requirements and safe recovery procedure. A file for a related Wi-Fi model, version `67/69`, different PID, or a different MG21 flash-size variant is reference material, not a conversion image.

The `analienx/gledopto` workflow demonstrates a read-only Tuya Cloud **GET** metadata/URL retrieval on an authorized Tuya-paired device. A Tongou adaptation can reuse the offline parser and authenticated firmware-download stage **only for a sacrificial, independently powered laboratory device**. It must not re-pair, power-cycle, reconfigure, or update an installed breaker. Never publish a signed expiring vendor URL or proprietary bytes without redistribution permission.

On receiving stock bytes: SHA-256 + file length first, standard OTA header and nested subelement lengths second, GBL tags/application properties third, then MG21 disassembly of the exact app to trace pin assignments, relay pulses, thermistor/ADC, independent controller communication, bootloader ABI and NVM layout. Keep all hypotheses labelled until matched against the physical PCB revision.

## Physical reverse-engineering ledger (exact revision only)

A disassembled product photo or pinout for `TO-Q-SY1-JWT`, `TO-Q-SYS-JWT`, or another Wi-Fi sibling is **not** evidence for the Zigbee `TO-Q-SY2-163JZT`. Do not transfer a claimed H-bridge, temperature-sensor net or relay GPIO from that image to this board. A sacrificial, confirmed-matching sample must be disconnected from mains and checked de-energized by a qualified person; housing damage/rivets and insulation are themselves safety-relevant.

| Subsystem | Required evidence | Why it blocks hardware use |
|---|---|---|
| ZSU and MCU | Exact marking, hardware revision, pad map, bootloader storage and flash size | No pin/flash-family substitution |
| Power-stage | Continuity-traced driver IC/transistors, two coil terminals, both drive paths and contact-state feedback; pulse timing from stock image | A speculative `RA4` relay GPIO can energize the wrong node or leave contacts unexpectedly closed |
| Meter | BL0942 IC/serial pins, shunt and voltage scaling, isolated reference and valid/invalid/stale failure mode | Unsaturated or stale measurement must not be interpreted as safe current |
| Thermal | Sensor part, net/ADC or separate MCU protocol, physical location and open/short behavior | Current app has no Tongou-qualified temperature input |
| Protection | Locate the overload/short-circuit/voltage/thermal trip mechanism, its supply and fail behavior without Zigbee MCU firmware | Electronic trip may disappear when replacing the application |
| Recovery | SWD access and readout protection, original image+configuration backup if readable, exact secure-boot/GBL policy and proven restore on a matching spare | An unsigned or wrong-geometry image could make the original protective function unrecoverable |

Tongou describes electronic threshold response on this model family and does not establish independence from the wireless processor; neither a product name nor a claimed IEC family standard proves autonomous short-circuit trip on this specific revision. Source: https://elcb.net/wp-content/uploads/2024/05/Smart-Electric-Portection-Device-Series-2024.pdf .

## Artifact classes (do not conflate)

- `A / simulator`: protocol and sensor tests only; safe to develop, not an MCU boot image.
- `B / isolated-module`: MG21-1024 debug/SWD **bench-only** application with no actuator GPIO, no stock image identity and no live OTA index. Before calling it flashable, independently verify the MCU part, linker load ranges and app properties; use only on an isolated disposable 3.3 V development module with an authorized recovery path.
- `C / sacrificial-board`: matching board and verified stock restore, relay/sensor mapping and protective architecture; isolated, qualified bench validation, never in a building distribution panel.
- `D / deployable protection device`: all prior evidence plus independent protection certification/qualified revalidation, controlled OTA rollback and fault-injection tests; absent today. No production flashing or broad public OTA index.

## Next execution order and stop conditions

1. Finish the PR's exact-byte BSEED Router/Client CI checks before integrating common-core changes. Preserve stock BSEED images unchanged. Keep Tongou `build: no`, custom-only provisional image type and null stock OTA IDs.
2. Make the native build *explicitly* choose the correct MG21-1024 part and an **inert, non-relay** laboratory config; capture `util appinfo`, ELF segment addresses, memory map, hashes, source revision and toolchain. Do not call a generic TS004F image a Tongou build. This stage is for a separate, isolated dev module only.
3. Perform offline Zigbee OTA/GBL forensic parsing of a legitimately acquired matching Tongou stock image and compare its app properties, version, sections, UART/ADC/relay code and signed/encrypted tags. The supplied stock-OTA artifact does not yet exist in this worktree.
4. Only if cryptographic acceptance or a controlled, backed-up SWD path has been *demonstrated* on a matching spare: qualify boot/recovery, correct both coil-drive paths, sensor calibration, NVM and Zigbee stock protocol against captured traffic.
5. Treat an unrecoverable debug lock, required unknown private signing key, unresolved independent trip architecture, or no matching sacrificial unit as an **explicit hardware stop**, not an opportunity to guess an OTA package for an installed breaker.

**Current critical path:** original v75 stock image OR matching de-energized sacrificial board with readable firmware/debug state; physical trip-chain verification; valid board-specific pin map and restore. Additional host-code coverage cannot replace these observations.

## Offline analysis tooling (first completed experiment)

`python helper_scripts/audit_tongou_ota.py PATH_TO_ORIGINAL.ota` prints SHA-256, lengths, outer Zigbee OTA tuple and subelement framing. A nested Silicon Labs GBL3 magic is reported **only as a magic-byte observation**, never as an authenticity check. Invalid/truncated framing fails closed; neither payload bytes nor private device IDs are printed. The tool is read-only, never constructs an OTA/GBL and always states `flash_approved: false`.

The parser was exercised **offline** on the hash-identified 208,946-byte Gledopto stock `.ota` already held in the separate project: manufacturer `0x124F`, image `0x1416`, version `0x28013001`, one 208,884-byte upgrade subelement; the embedded image is **not** GBL3. This verifies the same forensic tooling works on a real vendor Zigbee OTA envelope while proving absolutely nothing about the Tongou stock payload (which has not been obtained).

For a future Tongou sample: after this framing audit, independently examine the exact embedded GBL and its application properties with Silicon Labs `commander gbl parse --app ...` and `commander util appinfo ...`. Only an actual stock sample can reveal whether it contains a GBL, is signed/encrypted and how app version/flash layout compare. A valid wrapper or locally generated unsigned GBL is not evidence of the installed bootloader's key policy.
