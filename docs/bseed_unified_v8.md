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

The current release is:

```text
build:       1.2.5-bseedv8u3
fileVersion: 0x12053006 / 302329862
```

### TS0726 3-gang dimmer/switch

This target is built from the same V8 source tree, but it is a different firmware image:

```text
board:        SWITCH_BSEED_TS0726_3GANG
manufacturer: iedhxgyi
stock mfr:    _TZ3002_iedhxgyi
model:        TS0726-3-BS
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
| `0x12053006` | consolidated unified V8 release; runtime source unchanged from accepted `0x12053004` |

The accepted `0x12053004` canary demonstrated:

- successful OTA installation;
- preserved IEEE/network identity;
- preserved canonical device configuration;
- working relay ON/OFF behavior;
- stable network operation;
- plausible mains-voltage reporting;
- coherent numeric power/current/energy fields at true no-load;
- no genuine device-originated PM/ZCL/parser error pattern.

No special load was connected during the final acceptance window, so 0 W / 0 A was the physically correct result. The release decision intentionally treats this as sufficient for the consolidation step rather than repeatedly running artificial load tests.

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
8. byte-identity validation for reproducible output.

Local builds are useful for diagnostics, but are not deployment candidates.

## BSEED Zigbee2MQTT OTA index

Use the dedicated BSEED index for these two families:

```text
https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json
```

It deliberately contains only four exact lookup entries:

| Device state | Manufacturer name | OTA image type | Version exposed to updater |
|---|---|---:|---:|
| custom TS011F-PM | `b28wrpvx` | `43556` | `0x12053006` |
| stock TS011F-PM | `_TZ3000_b28wrpvx` | `54179` | `0xFFFFFFFF` |
| custom TS0726 | `iedhxgyi` | `45577` | `0x1102300a` |
| stock TS0726 | `_TZ3002_iedhxgyi` | `54179` | `0xFFFFFFFF` |

The repository's generic router index is also sanitized during publication: stale entries for these exact BSEED manufacturer names are removed and replaced with the same four current entries. Historical BSEED FORCE entries are removed rather than silently exposing an old firmware build.

## Stock Tuya -> custom conversion

The conversion mechanism is inherited from the upstream Romasku build model, but the BSEED release scripts make the relationship explicit and testable.

For each target GitHub Actions compiles **one Telink firmware binary** and then wraps that same payload twice:

1. the normal custom -> custom image uses the custom firmware image type and the real custom file version;
2. the stock -> custom `from_tuya` image uses the stock Tuya image type `54179` and outer file version `0xFFFFFFFF`.

For the supported targets the stock identities are:

```text
TS011F-PM: _TZ3000_b28wrpvx / TS011F
TS0726:    _TZ3002_iedhxgyi / TS0726
```

The release validator requires the normal and `from_tuya` OTA files to be the same length and byte-identical from byte 56 onward. The only permitted differences are OTA-header offsets 12-17: image type and file version. This prevents a conversion wrapper from silently containing a different compiled firmware payload.

After a successful stock conversion the device boots the normal custom firmware identity, so later updates are served by the custom image type rather than the `0xFFFFFFFF` stock wrapper.

### Zigbee2MQTT outline

1. Verify the **exact** stock manufacturer name and model, not only the enclosure.
2. Configure the BSEED OTA override index above.
3. Restart Zigbee2MQTT if required for the index change.
4. Check for an OTA update on the exact target device.
5. Confirm that the offered entry corresponds to the correct BSEED family before starting the transfer.
6. Run the OTA update without interrupting mains power.
7. After reboot, interview/reconfigure the device if Zigbee2MQTT needs to refresh clusters/exposes.
8. Confirm the new custom build identity and normal device behavior.

## Conversion and recovery boundary

Publishing a stock-facing OTA wrapper does **not** make stock conversion reversible.

If a full original-firmware backup and tested restore procedure do not exist for that exact hardware revision, treat conversion as potentially one-way. The stock wrapper is deliberately restricted by exact manufacturer name in the BSEED index to reduce the chance of offering it to a merely similar TS011F or TS0726 device.

The software packaging path is validated by GitHub Actions. A stock-hardware conversion canary remains a separate hardware-validation boundary and should not weaken the existing requirement for a proven recovery/restore route before intentionally mutating a pristine stock unit.

## Updating an already-custom PM socket

A second PM socket with the same exact hardware identity and canonical configuration is the same firmware target. It does not require a different binary merely because it is a different physical unit.

Before updating, verify the device identity and configuration. Do not infer compatibility from the enclosure or a generic `TS011F` label.

## Upstream relationship

The fork follows the architecture of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch). BSEED-specific changes are kept bounded and reviewable so useful pieces can be upstreamed where appropriate, instead of creating an unrelated replacement firmware project.
