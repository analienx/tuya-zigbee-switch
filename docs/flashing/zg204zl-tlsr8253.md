# HOBEIAN / Tuya ZG-204ZL PIR + lux: TLSR8258 SWire / Zigbee OTA preflight

**Status: inspection-only. No device flash, erase, OTA update, or production deployment authorized by this document.**

## Ownership and hardware

This is an extension to the existing `telink_swire_recovery.py` tool; it does **not**
inherit the BSEED/TLSR8258 write rules. Its initial `zg-204zl` profile exposes
pinout and diagnostic discovery and blocks `prepare` / writer admission.
Do not substitute `generic-tlsr8258`: the chip now matches the generic silicon family, but this PIR's flash boot scheme and NVM/IEEE ranges have not been proved equivalent to the BSEED dual-slot map.

Board reference: https://pvvx.github.io/ZG-204ZL-3.0/

- SoC: **TLSR8258F512ET32** physically photographed on Hall `ZG-204ZL-3.0` board. pvvx's hardware webpage previously states TLSR8253, while the upstream firmware makefile compiles `MCU_CORE_8258=1`.
- 5-pin connector: **1 = +Vbat**, **2 = GND**, **3 = PA7/SWS**, **4 = PB1/TX**, **5 = PB7/RX**
- Chip-side view with five holes at top and button upper-right, **left-to-right:** 5 RX, 4 TX, 3 SWS, 2 GND, 1 +Vbat (reverse side mirrored).
- The board uses an **XC6206 2.1 V LDO**. **Never** inject 3.3 V into its
  **2.1 V output**. +Vbat is the battery input, not a generic 3.3 V rail:
  verify permissible battery voltage and polarity for the exact board first.
- **Voltage-level warning:** the TLSR8258 SWS pin must stay within its
  actual GPIO supply voltage limits. The generic CP2102 TXD output is ~3.3 V,
  but this board may supply its GPIO at 2.1 V. A 1.2 kOhm resistor is
  **not** a voltage-level converter. Do **not** wire TXD/SWS until the
  measured SWS I/O supply and an appropriate compatible level-shifting
  interface are verified. See Telink TLSR8258 maximum input-pin limit
  V_IN <= VDD + 0.3 V and pvvx's warning about UART overvoltage.
  The prior TLSR8253-specific datasheet link is NOT an authority for the photographed TLSR8258F512ET32; consult the exact Telink TLSR8258F512ET32 electrical limits.
  https://github.com/pvvx/ZigbeeTLc/issues/161
- Remove the coin cell when using an externally powered bench setup.
- The 3-wire contact arrangement for SWS, ground and battery input is not a
  promise that adapter-sourced power is adequately regulated/current-limited.

The available `cp2102-single-sws` circuit uses TXD through a **1.2 kOhm**
resistor to the RXD/SWS junction on already qualified 3.3 V target circuits.
This is a reference for the *adapter topology*, **not a proven safe voltage
interface for the ZG-204ZL**. Only **one SWS contact** reaches the PCB once
the signal level has been qualified.
An observed adapter TX↔RX echo is *not* target SWire communication. An RX
indicator LED, loaded RX pin, poor pogo contact, or unsupported power supply
can prevent bidirectional reads.

## Firmware: two distinct OTA identities

Upstream: https://github.com/pvvx/ZigbeeTLc/tree/master/bin

| Purpose | Upstream file | Header identity |
|---|---|---|
| Factory Tuya/Sonoff → ZigbeeTLc **candidate conversion OTA** | `1286-0203-99993001-ZG204ZLz_v0140.zigbee` | Manufacturer 0x1286; image type 0x0203 |
| Subsequent **ZigbeeTLc OTA** | `1141-0232-01403001-ZG204ZL.zigbee` | Manufacturer 0x1141; image type 0x0232 |
| Native **programmer** image | `ZG204ZL_v0140.bin` | Not a Zigbee OTA container |

These filenames are identified from pvvx v0.1.4.0 source and build scripts;
they are **not yet locally downloaded, hash-pinned, or hardware-accepted**.
Never feed a `.zigbee` container directly into a SWire writer, and do not
assume a native `.bin` is compatible with the existing TLSR8258 dual-slot
`KNLT` image validator.

The original board reference reports 0x1286/0x0203; a label such as
`ZG-204ZL` is insufficient to verify stock OTA eligibility. Before deciding
whether conversion OTA can be offered, read **the live device's** manufacturer
code, image type, file version, hardware revision, and manufacturerName from
Zigbee2MQTT, then compare the actual candidate's parsed OTA header and its
acceptance/version rules. No force/downgrade/override index until established.

After successful conversion, Zigbee2MQTT recognizes `ZG-204ZL-z` as
OTA-capable. Re-interview and Zigbee bindings may be affected, so preserve
the device's existing automation/binding topology before any conversion.

## Existing Windows toolkit — diagnostics only

Installed location (operator workstation): `C:\Workspace\tools\telink-swire`.
This is a *local deployed copy* of the upstream tool in this repository.
Check out the candidate code separately; do not overwrite a running recovery
kit or copy an unreviewed branch into the deployed tool.

Start with **nothing physically connected to the PIR**:

```text
python telink_swire_recovery.py capabilities --json
python telink_swire_recovery.py ports --json
python telink_swire_recovery.py wiring --device zg-204zl --connection cp2102-single-sws
python telink_swire_recovery.py fetch-reader --json
```

The initial profile deliberately blocks `test-link --phase target` and
`watch-link` before opening any port (`SWIRE_VOLTAGE_UNQUALIFIED`).
Those operations need an independently qualified target-voltage interface
and a follow-up narrowly reviewed implementation; **do not bypass this profile**
or connect 3.3 V TXD to SWS directly. Private receipts/dumps must remain
**outside Git**.
The generic `read` command uses a SHA-pinned pvvx TLSR825x reader; confirm
actual flash JEDEC capacity before requesting a full read, keep two complete
matching reads, and retain factory/IEEE/calibration material. Its success
requires genuine device responses; no TX-only substitution counts.

Important: the existing writer's fixed slots (`0x00000..0x33fff`,
`0x40000..0x73fff`) and reserved regions were validated for **TLSR8258
BSEED boards**. They have not been qualified for this TLSR8258 PIR.

## Gates to enable writing later

1. Confirm exact PCB/chip and connector orientation on the selected sensor.
2. Demonstrate **target** SWire replies, JEDEC capacity and two identical
   full reads. The adapter loopback alone is not acceptable.
3. Decode factory flash: boot marker/slot layout, NVM, IEEE identity, factory
   calibration and any secure/application settings, with independent evidence.
4. Validate the exact pvvx image by immutable commit + SHA-256, header/native
   image consistency and supported flash geometry.
5. Implement an **independent TLSR8258** flash layout strategy and fail-closed
   CI simulations, including brownout/lost-write and preservation tests.
6. Recheck live Zigbee2MQTT state and OTA concurrency; do one noncritical
   physical sensor first, then verify occupied/unoccupied states, lux and
   battery readings, timeout behavior, sleep current and rejoin behavior.
7. Only then approve a device-specific write/OTA operation. No silent pairing
   reset, erase-all, firmware FORCE, or multi-device rollout.

### External references
- Hardware/connector: https://pvvx.github.io/ZG-204ZL-3.0/
- Firmware: https://github.com/pvvx/ZigbeeTLc
- Firmware build conversion metadata: https://github.com/pvvx/ZigbeeTLc/blob/master/WinMakeZ.cmd
- Zigbee2MQTT custom-device firmware support: https://www.zigbee2mqtt.io/devices/ZG-204ZL-z.html
- Pinned-reader details: https://github.com/pvvx/TlsrComSwireWriter
