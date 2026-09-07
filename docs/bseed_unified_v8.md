# BSEED unified V8

This page documents the BSEED-specific integration maintained in this fork.

## Purpose

The integration is designed to avoid maintaining one private firmware fork per physical device. Instead, BSEED targets share the common V8 firmware architecture while keeping the parts that must remain hardware-specific separate:

- board configuration;
- GPIO/pin assignments;
- Zigbee OTA manufacturer/image type;
- firmware version sequence;
- device configuration guard;
- target-specific clusters and features.

The result is a shared source tree with independently built target images.

## Supported BSEED targets

### TS011F-BS-PM power-monitoring socket

Validated target:

```text
board:        OUTLET_BSEED_PM_TS011F
manufacturer: b28wrpvx
model:        TS011F-BS-PM
config:       b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;
MCU:          Telink TLSR8258
OTA mfr:      4417
OTA image:    43556
```

Power monitoring uses a BL0937 with the HLW8012-compatible pulse backend:

```text
CF:   PA1
CF1:  PC2
SEL:  PB1
```

Validated calibration constants:

```text
voltage: 161460
current: 144679
power:   16989
```

The current release candidate is:

```text
build:       1.2.5-bseedv8u3
fileVersion: 0x12053006 / 302329862
```

### TS0726 3-gang dimmer/switch

This target is built from the same exact V8 source revision, but it is a different firmware image:

```text
board:        SWITCH_BSEED_TS0726_3GANG
OTA mfr:      4417
OTA image:    45577
build:        1.1.8-bseedv8
fileVersion:  0x1102300a / 285356042
```

Do not cross-flash the two images.

## What the unified V8 keeps

The BSEED integration intentionally keeps the newer common architecture rather than reverting to an older device-specific firmware tree. That includes:

- shared Telink platform and Zigbee infrastructure;
- configuration parser/resource preflight hardening;
- NVM migration support and migration-version persistence;
- image-type ownership/collision checks;
- target-specific OTA identities;
- protection plumbing for the PM socket;
- standard electrical-measurement/metering clusters;
- the existing TS0726 V8 behavior and ABI;
- reproducible GitHub Actions builds with the pinned real Telink TC32 toolchain.

For the PM socket, the measurement loop uses the hardware-proven predecessor BL0937 sampling semantics inside the newer V8 platform. This was done after a V8 PM canary showed that the platform was healthy but the newer PM behavior did not provide acceptable metering state. The corrected implementation was then revalidated on the same physical socket.

## PM canary history and version sequence

The PM version sequence is intentionally explicit:

| Version | Purpose |
|---|---|
| `0x12053003` | known-good predecessor/recovery state used to recover the canary |
| `0x12053004` | V8 PM fix canary; successfully installed and accepted on `WorkroomSocketCabinet` |
| `0x12053005` | sealed known-good recovery successor; intentionally reserved, not a normal release |
| `0x12053006` | consolidated unified V8 release candidate; runtime source unchanged from accepted `0x12053004` |

The accepted `0x12053004` canary demonstrated:

- successful OTA installation;
- preserved IEEE/network identity;
- preserved canonical device configuration;
- working relay ON/OFF behavior;
- stable network operation;
- plausible mains-voltage reporting;
- coherent numeric power/current/energy fields at true no-load;
- no genuine device-originated PM/ZCL/parser error pattern.

No special load was connected during the final acceptance window, so 0 W / 0 A was the physically correct result. The release decision intentionally treats this as sufficient for the current consolidation step rather than repeatedly running artificial load tests.

## Build and release policy

Deployable firmware for these targets is produced by GitHub Actions only.

A release candidate must pass on one exact source SHA:

1. host tests;
2. lint;
3. firmware image-type collision checks;
4. focused BSEED PM/NVM/parser/layout regression tests;
5. real pinned Telink TC32 build of the TS011F PM image;
6. real pinned Telink TC32 build of the TS0726 image from the same SHA;
7. OTA header, manufacturer, image type, version, size and manifest validation;
8. a second PM build that is byte-identical to the first.

Local builds are useful for diagnostics, but are not deployment candidates.

## Updating an already-custom PM socket

A second PM socket with the same exact hardware identity and canonical configuration is the same firmware target. It does not require a different binary merely because it is a different physical unit.

Before updating, verify the device identity and configuration. Do not infer compatibility from the enclosure or a generic `TS011F` label.

## Stock Tuya devices

This project does **not** claim that every stock BSEED/Tuya device can be safely converted simply because a matching-looking custom image exists.

For stock devices, a safe conversion decision should account for the ability to restore the exact original firmware. If a full backup and tested restore procedure are unavailable for that hardware, the conversion is effectively one-way and carries a materially higher brick/recovery risk.

The risky PM firmware canaries used to validate this integration were therefore performed on already-custom devices, not on pristine stock Tuya units.

## Upstream relationship

The fork follows the architecture of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch). BSEED-specific changes are kept bounded and reviewable so useful pieces can be upstreamed where appropriate, instead of creating an unrelated replacement firmware project.
