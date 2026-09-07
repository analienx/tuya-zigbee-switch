# Tuya Zigbee Switch — BSEED unified V8 fork

This public fork of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch) carries a tested BSEED-focused integration on top of the upstream Telink/Silabs custom-firmware project.

The goal is **one maintainable common firmware core with separate, hardware-specific images**. The current integration focuses on two BSEED device families that we use and validate on real hardware:

| Target | Firmware board | OTA identity | What it adds |
|---|---|---|---|
| BSEED TS011F power-monitoring socket (`b28wrpvx`) | `OUTLET_BSEED_PM_TS011F` | manufacturer `4417`, image type `43556` | Relay control, BL0937 power monitoring, energy metering, protection plumbing, config/NVM migration and OTA |
| BSEED TS0726 3-gang dimmer/switch | `SWITCH_BSEED_TS0726_3GANG` | manufacturer `4417`, image type `45577` | BSEED TS0726 control/configuration behavior on the shared V8 core |

**These are separate binaries. Never flash the TS011F-PM image onto a TS0726 device, or vice versa.**

## Why this fork exists

The BSEED work started from hardware-proven custom firmware and was consolidated into the newer common V8 architecture instead of maintaining divergent device-specific forks. The integration keeps useful upstream/Romasku fixes and adds additional hardening around configuration parsing, NVM migration, image-type ownership, reproducible builds and BSEED power monitoring.

For the TS011F PM target, the V8 platform uses the BL0937/HLW8012-compatible measurement behavior that has been proven on the target hardware, while retaining the newer common-core Zigbee, OTA, configuration and migration infrastructure.

See [docs/bseed_unified_v8.md](docs/bseed_unified_v8.md) for the architecture, supported identities, firmware versions, validation model and recovery policy.

## Current validated release candidate

The unified V8 release candidate is built from one exact source revision and produces both target images:

- **TS011F-BS-PM socket:** `1.2.5-bseedv8u3`, file version `0x12053006`
- **TS0726 3-gang:** `1.1.8-bseedv8`, file version `0x1102300a`

The PM socket candidate is functionally identical at runtime to the immediately preceding `0x12053004` hardware canary; `0x12053005` is intentionally reserved as a known-good recovery slot.

Every deployable candidate is produced by **GitHub Actions**, not by an ad-hoc local compiler output. The release gate includes normal tests/lint, image-type collision checks, real pinned Telink TC32 builds of both targets, OTA-header/manifest validation and a second byte-identical PM rebuild.

## Safety and hardware identification

BSEED sells many visually similar devices with different internals. Support is based on the **exact firmware board/configuration and Zigbee identity**, not the product faceplate alone.

For the validated PM socket target the canonical configuration is:

```text
b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;
```

Do not assume another TS011F or another BSEED socket is compatible merely because the enclosure looks the same.

### Stock firmware warning

For devices that still run original Tuya firmware, do not treat possession of a custom OTA image as a complete recovery strategy. If a full original-firmware backup and tested restore path do not exist for that exact hardware, conversion can leave no reliable way back. The validated release work in this fork intentionally used already-custom hardware for risky firmware canaries.

## Documentation

- [BSEED unified V8 architecture and release notes](docs/bseed_unified_v8.md)
- [Supported devices](docs/supported_devices.md)
- [OTA updating](docs/updating.md)
- [Firmware changelog](docs/changelog_fw.md)
- [Known issues](docs/known_issues.md)
- [Contributing / porting](docs/contribute/porting.md)

## Upstream

This repository is a fork of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch). The intention is to keep the BSEED additions reviewable and suitable for upstreaming where their scope fits the upstream project, rather than replacing the upstream architecture with a private one-off firmware tree.
