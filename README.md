# BSEED Zigbee Firmware

Custom Zigbee firmware and release tooling for three exact BSEED device families, maintained as a hardware-focused fork of [Romasku's Tuya Zigbee Switch](https://github.com/romasku/tuya-zigbee-switch).

[![Tests](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml)
[![PM role matrix](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml)
[![Telink Router build](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/telink-router-ci.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/telink-router-ci.yml)

> [!CAUTION]
> Firmware support depends on the exact Zigbee manufacturer and model identifiers below, not the enclosure or retail name. Custom firmware can make a mains-powered device unusable, and stock firmware recovery may not be possible. Confirm the identity and read the [installation guidance](docs/updating.md) before updating.

## Supported BSEED hardware

The public [BSEED OTA index](https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json) currently offers Router firmware and stock-to-custom conversion for these targets:

| Device | Exact Zigbee identity | Published Router firmware | Custom image type |
|---|---|---|---:|
| TS011F-BS-PM power-monitoring socket | `b28wrpvx / TS011F-BS-PM` | `1.2.5-bseedv8u4` · `0x12053007` | `43556` |
| TS011F-BS non-PM socket | `o1jzcxou / TS011F-BS` | `1.1.3-bseedv8` · `0x11023001` | `43555` |
| TS0726 3-gang switch/dimmer | `iedhxgyi / TS0726-3-BS` | `1.1.8-bseedv8` · `0x1102300a` | `45577` |

The index contains the matching stock conversion entries for these targets. Each device family has a distinct image; never cross-flash between them.

**Mains Client (EndDevice) firmware is experimental and is not published in the BSEED index.** The PM `1.2.5-bseedcli19` (`0x1205301e`) and non-PM `1.1.3-bseedc13` (`0x1102301a`) parent-recovery candidates have passed offline source/build checks, but still need hardware acceptance. Do not install them as releases. See the [Client recovery review](docs/bseed_client_parent_recovery_20261013.md) and [current release status](docs/bseed_socket_release_status.md).

## What this fork adds

- **PM metering:** validated BL0937 voltage, current, power, and cumulative energy reporting on the TS011F-BS-PM.
- **Independent relay behavior:** physical output can follow logical Zigbee state or remain on/off, supporting smart-bulb and detached-control setups.
- **Router reliability:** recovery uses the Telink SDK's rejoin/backoff path without colliding with fresh-device commissioning. The PM Router's sleepy-child parenting was hardware-tested with an IKEA RODRET E2201.
- **Client recovery work:** PM and non-PM candidates detect repeated failed parent polls and request SDK-owned recovery with staggered retries. This remains a firmware candidate, not a hardware-accepted release.
- **BSEED integration:** target-specific pinouts, migration safeguards, stock conversion packages, Zigbee2MQTT support, and read-only diagnostics.

The reusable switch framework remains Romasku-derived. This fork keeps its BSEED-specific behavior and release tooling identifiable and preserves upstream attribution and licensing.

## Get started

1. Match the device's exact manufacturer/model identity and release status in the [BSEED target table](docs/bseed_socket_release_status.md).
2. Read [OTA conversion and update guidance](docs/updating.md) and the [known issues](docs/known_issues.md).
3. Configure Zigbee2MQTT to use the BSEED index and restart it:

   ```yaml
   ota:
     zigbee_ota_override_index_location: >-
       https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json
   ```

4. Confirm the offered image matches the exact device and intended role before starting an update.

The index publishes Router images and stock conversion paths only. It does not publish Client images or cross-role transitions. Stock-to-custom conversion should be treated as potentially one-way unless you have an exact-device backup and a tested restore path.

## Smart-bulb setup

Logical Zigbee state, physical relay output, button behavior, and indicator LEDs can be configured independently. For example, a channel can keep a smart bulb powered while its wall button sends Zigbee commands:

```text
physical relay output = always_on
logical Zigbee state  = independently controlled
button action         = bound to the smart bulb
indicator LED         = manually controlled
```

`detached` only controls whether a local button press operates the relay; it does not pin the electrical output on or off.

## Release quality

Deployable firmware is built by GitHub Actions from a clean source revision. Release workflows check host tests, firmware identities and OTA headers, the pinned Telink toolchain, and reproducibility where required. A passing CI run is offline evidence; it does not replace device-specific hardware acceptance.

## Documentation

| Topic | Guide |
|---|---|
| Exact targets, identities, and architecture | [BSEED unified V8](docs/bseed_unified_v8.md) |
| Installation and OTA updates | [Updating OTA](docs/updating.md) |
| Published and candidate firmware status | [BSEED socket release status](docs/bseed_socket_release_status.md) |
| Client parent recovery review | [Recovery implementation and evidence](docs/bseed_client_parent_recovery_20261013.md) |
| Supported device database | [Supported devices](docs/supported_devices.md) |
| Firmware changes | [Firmware changelog](docs/changelog_fw.md) |
| Known limitations | [Known issues](docs/known_issues.md) |
| Contributing and hardware porting | [Contributing](CONTRIBUTING.md) · [Porting guide](docs/contribute/porting.md) |
| Support | [Support guidance](SUPPORT.md) · [Security policy](SECURITY.md) |

See [LICENSE](LICENSE) for licensing terms and the [upstream project](https://github.com/romasku/tuya-zigbee-switch) for the generic firmware framework.
