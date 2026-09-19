# TS0505B hardware identification and recovery — evidence contract

> **Superseded hardware hypothesis:** the three RGB+CCT modules have since been reported as ZTU (Telink Z2/TLSR8258-compatible). The ZSU/SWD/MG21 procedure below applies **only** to a separately confirmed ZSU device, NOT to the ZTU bulbs. Use [ZTU flash-image plan](ztu-telink-flash-plan.md) and the Telink SWire interface instead.

**Status: external reference only; no production bulb has been opened, electrically traced, or identified.** The stock Zigbee identity identifies a product family, not its exact chip, module, bootloader, or LED-driver board.

## 1. Module candidate, not measured target

Tuya's **ZSU module datasheet** describes EFR32MG21A020F1024IM32-B, 1,024 KiB flash and 96 KiB RAM. Its lighting development kit permits both EFR32MG21A020F1024IM32 and EFR32MG21A020F768IM32 as `chip_id` choices. Consequently neither the Zigbee model/manufacturer fingerprint nor a successful 768/1024 SDK build establishes the installed flash density.

- Module datasheet: https://developer.tuya.com/en/docs/iot/ZSU?id=Kapoo3t83vl7c
- Tuya lighting kit: https://developer.tuya.com/en/docs/iot-device-dev/tuyaos_zigbee_light_product_development_kit?id=Kd6efghkquo9d

The module drawing/pad numbering below applies **only if** a safely inspected physical module is positively identified as the corresponding ZSU revision. Never use the ZSU diagram as a blind connection map on another module.

## 2. Crucial reference-pin discrepancy

Tuya's generic lighting-kit example assigns R=PA3, G=PD2, B=PC5, CW=PA4, WW=PA0. Its ZSU module datasheet instead classifies the exposed PD02 pad (pad 17) as **ADC input** and lists dedicated PWM support on PB01, PB00, PA00, PA03 and PA04 (module pads 8–12). PC05 (pad 2) is described as general I/O. This is a documentation-level mismatch, **not** proof that the target board uses a particular driver or that the MG21 die cannot route PWM to an alternative pad.

**Engineering consequence:** the five example GPIO names are not an acceptable production lighting map. Before enabling *any* output, identify the physical module and trace each actual driver-input connection, polarity, PWM frequency, power limiting, default-at-reset state and channel order on the specific board. The current dark candidate intentionally drives none of them.

## 3. Read-only debug access, conditional on identifying the module

The ZSU datasheet assigns SWDIO=module pad 3 (PA02), SWCLK=pad 4 (PA01), GND=pad 13, 3.3 V VCC=pad 14, and active-low NRST=pad 18. Its note warns that programming pads may not be exposed by the host PCB. These are **module-side** pads; access on the finished lamp is unverified.

**Electrical safety:** a mains-powered LED lamp may have a non-isolated low-voltage rail and charged capacitors after unplugging. Never attach a laptop, debug probe or USB ground to an assembled lamp on mains. Do not test on the live driver board. Physical opening, capacitor discharge, isolation verification and module-only low-voltage power must be handled by a qualified person using suitable equipment; if the module cannot be safely separated, stop.

Read-only Silicon Labs Commander commands, once an independently isolated module and a supported debug probe are available:

```text
commander adapter list
commander device info --noreset --tif SWD --serialno <probe-serial>
commander security status --noreset --tif SWD --serialno <probe-serial>
```

`device info` can identify the silicon family and flash capacity; `security status` can report secure boot and debug lock. Neither command proves that the **application bootloader** accepts the custom unsigned GBL, nor that a stock image can be restored. No `flash`, `erase`, `unlock`, `writekey`, `bootloader`, `reset`, or other mutating Commander commands are part of this procedure.

If debug is unlocked and a qualified operator confirms a non-destructive read is possible, preserve a private *complete* per-device flash/NVM/configuration backup before any change, then validate its byte count and SHA-256 twice. A successful debug-ID read is **not** a successful stock-firmware backup. Debug unlock may erase flash on locked devices: do not try it as a recovery shortcut.

Silicon Labs security reference: https://docs.silabs.com/shared-content/1.0.6/prod-programming-series2-and-series3/10-enabling-debug-lock

## 4. What must be observed on one physical specimen

- Photos of the exterior product identification, controller module marking (both sides), and LED driver PCB (both sides), with personal labels/serial numbers excluded from any public issue or PR.
- Exact physical SoC/flash variant from chip marking or read-only probe; separate evidence of bootloader slot placement, NVM area and OTA storage capacity on **that specimen**.
- Debug-lock and Secure Boot status; separately, bootloader GBL signature/encryption and rollback policy. Secure Boot status alone does not establish GBL acceptance policy.
- Recovery evidence: intact stock backup *and* independently validated restoration method, or vendor-supplied exact stock firmware with known-good restoration path. Keep backups and keys private.
- Channel-to-module-pad-to-driver trace for R/G/B/CW/WW, polarity, safe reset levels, PWM frequency, current/power limits, and all-off startup. Do not infer these from the development-kit sample.

**Current actual access:** on 2026-09-19 the authorized local workstation reported `deviceCount=0` through Commander `adapter list`, with no present J-Link/ST-Link/CMSIS-DAP device. No physical module was inspected, no SWD access was attempted and no flash memory was read. This is a tooling availability fact, not evidence that the bulb's debug port is locked.
