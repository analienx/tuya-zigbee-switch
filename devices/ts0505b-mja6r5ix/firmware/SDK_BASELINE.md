# Firmware SDK baseline

## Structural build generation

The Silicon Labs reconstruction track is now pinned and build-proven with:

- Silicon Labs Simplicity SDK: **2026.6.1**
- Zigbee / EmberZNet stack: **9.1.1**
- architecture baseline: official Silicon Labs **Z3 SoC Light** template
- template: `zigbee_app/z3/zigbee_z3_light/zigbee_z3_light.slcp`
- structural target: `EFR32MG21A020F1024IM32`
- SLC: **6.0.23**
- GCC: **14.2.1** (`silabs-14.2.rel1-b66`)
- CMake used by the generated build: **3.30.2**
- Ninja: **1.12.1**
- ZAP: **2026.6.18**
- Simplicity Commander: **1v24p3b1989**, post-build dependency only

The MG21 part is a **family-reference compile envelope**, not direct proof of the exact silicon/flash density fitted to `_TZ3210_mja6r5ix`. No disassembly or debug-port identification is allowed by project policy.

## Why the vendor Z3 Light baseline

The vendor application already supplies the relevant Zigbee architecture: router-capable Zigbee PRO stack, source-route support, stack diagnostics and counters, standard light clusters, network steering, reporting, NVM-backed state, and application-bootloader integration. The custom project remains a narrow board/application adaptation plus reviewed routing configuration, not a home-grown Zigbee stack.

## Board-neutral generation policy

Generate from the official template, add the exact structural part plus `iostream_rtt`, remove only the template's development-board `simple_led` and `simple_button` components, and apply reviewed configuration through SLC. Do not invent PWM, PTI, UART, LED or button pins for the production bulb.

Router-v0 currently contains one deliberate routing override:

```text
SL_ZIGBEE_NEIGHBOR_TABLE_SIZE=26
```

Route=16, discovery=8, address=12 and broadcast=15 remain at the reviewed/default values. The optional concentrator component is absent and the bulb must not originate periodic many-to-one route requests.

## Reproducibility checkpoint

On 2026-09-10 the routing-reference and router-v0 projects both compiled and linked successfully. A second clean router-v0 generation directly from the official SDK template produced a byte-identical application BIN.

See `firmware/silabs_router_v0_build_manifest.json` and `evidence/silabs-router-v0-build-2026-09-10.md` for exact hashes, tool versions and memory accounting.

This proves the **software build path**, not deployment compatibility. The generated Silicon Labs application is still non-deployable because the installed bulbs' live OTA identity is `0x100B/0x020C`, the current file version is `0x10003607`, the Tuya/OEM bootloader/container contract is unresolved, and no verified rollback image exists.

The separate TuyaOS lighting-framework track remains preferred for vendor application/UG packaging evidence when that framework becomes available; a generic Tuya `0x1002/0x1602` build must never be used as the installed target.
