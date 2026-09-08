# Tuya Zigbee Switch — BSEED unified V8

Hardware-focused custom Zigbee firmware for selected BSEED devices, built on top of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch).

[![CI](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml)
[![Reproducible firmware](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml)
[![BSEED OTA distribution](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/bseed-ota-distribution.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/bseed-ota-distribution.yml)

> [!IMPORTANT]
> This repository supports **exact hardware/Zigbee identities**, not product appearance alone. BSEED sells visually similar devices with different internals. Verify the target before installing firmware.

## Why this fork is useful

Romasku already provides an excellent generic base: fast local actions, ordinary detached mode, outgoing Zigbee binds, long-press handling, multiple switch types, power-on behavior, Router/EndDevice builds and stock-to-custom OTA.

This fork keeps those capabilities and adds the BSEED-specific pieces that matter in day-to-day use.

### Features users actually notice

| Feature | What it means in practice | Compared with Romasku `main` |
|---|---|---|
| **Physical relay behavior independent of Zigbee state** | Each relay can `follow_state`, stay `always_on`, or stay `always_off` while its logical Zigbee On/Off state remains independently controllable. This is especially useful with smart bulbs, remote-only buttons and safety/override use cases. | **Fork addition.** Upstream detached mode controls whether a local button operates the relay, but does not provide this separate pinned-output policy on `main`. |
| **Working BL0937 power monitoring on the BSEED TS011F PM socket** | Standard voltage, current, active power and cumulative energy reporting instead of using the socket as relay-only hardware. | **Implemented and hardware-validated here.** Upstream currently lists power monitoring as work in progress. |
| **BSEED TS0726 canonical migration** | Existing devices can move from the historical swapped-pin workaround to the canonical BSEED mapping without throwing away the stored configuration/state model. | **BSEED-specific.** This migration is not part of generic upstream firmware. |
| **Per-channel indicator LED control** | Indicator LEDs can follow the logical relay state, show the inverse, or run in `manual` mode where the LED state is controlled separately. | The generic LED primitive already exists upstream; **this fork validates it on the canonical BSEED mapping and uses it as part of the BSEED control model.** |
| **Router or always-awake Mains Client role** | A mains-powered device can either strengthen the mesh as a normal Router or behave as an always-reachable non-routing leaf. Mains Client is not a sleepy battery EndDevice: its receiver stays on and Poll Control/power-management assumptions are removed. | **Fork-specific BSEED role profile.** The candidate is software/reproducibility validated; hardware canary is the final promotion gate. |
| **BSEED-focused Zigbee2MQTT UX** | The tested BSEED deployment layout uses human terms and ordering: logical state → physical relay behavior → button behavior → indicator behavior/state → advanced hardware configuration, with Left/Middle/Right semantics instead of endpoint decoding. | **Target-specific BSEED integration work.** The generic generated converter in this repository remains upstream-compatible, so not every deployment-level label/layout improvement has been folded into the generic converter yet. |
| **Exact stock-Tuya conversion packages** | The two supported stock identities have dedicated, validated `from_tuya` wrappers and a small BSEED-only OTA index. | Upstream has the generic conversion mechanism; **this fork adds exact BSEED packaging, validation and index hygiene.** |
| **Release-grade reproducibility** | Deployable images come only from GitHub Actions, both BSEED targets are built from the same SHA, OTA identities are checked and PM output is rebuilt byte-for-byte. | **Fork-specific release policy and CI.** |

The comparison above is intentionally conservative: this project reuses upstream features when upstream already has them instead of relabeling them as fork inventions.

### The smart-bulb / remote-control use case

The key distinction is that **button behavior, logical Zigbee state and physical mains output are separate concepts**.

For example, a BSEED channel can be configured so that:

```text
physical relay behavior = always_on
logical relay state     = independently controlled by Zigbee
button action           = sends commands/events to bound smart lights
indicator LED behavior  = manual
indicator LED state     = controlled separately when desired
```

That means a smart bulb can stay permanently powered while the wall control still behaves like a proper Zigbee remote, and the panel LED does not have to be tied to the electrical relay. In `manual` LED mode the firmware deliberately leaves LED state independent; synchronizing it to a remote light can then be done explicitly if desired.

This is different from ordinary `detached` mode: detached mode only decides whether the **local button press** drives a relay. The physical relay policy decides whether the **electrical output itself** follows logical state or is pinned ON/OFF.

## What this fork adds under the hood

- **One maintained V8 core, separate hardware images** for the supported BSEED families.
- **Two mains-powered Zigbee roles**: Router and an always-awake, non-routing Mains Client built from the same application core.
- **BL0937 power monitoring** for the validated TS011F PM socket target, using hardware-proven sampling semantics.
- **BSEED TS0726 support** on the same common-core architecture.
- **Physical-output policy** kept orthogonal to logical relay state.
- **Stock-Tuya → custom OTA wrappers** for the exact supported stock identities.
- **Safer configuration and NVM handling**, including bounded parsing and migration guards.
- **Actions-only deployable artifacts** with pinned real-Telink builds, OTA-header checks and byte-for-byte reproducibility gates.

## Supported BSEED targets

| Device family | Exact identity | Board | Current Router firmware | Router OTA image type |
|---|---|---|---|---:|
| TS011F power-monitoring socket | `b28wrpvx / TS011F-BS-PM` | `OUTLET_BSEED_PM_TS011F` | `1.2.5-bseedv8u3` · `0x12053006` | `43556` |
| TS0726 3-gang switch/dimmer | `iedhxgyi / TS0726-3-BS` | `SWITCH_BSEED_TS0726_3GANG` | `1.1.8-bseedv8` · `0x1102300a` | `45577` |

Both custom images use manufacturer code `4417`.

**The two hardware targets use different binaries. Never flash a TS011F-PM image onto a TS0726 device, or vice versa.**

## Router vs Mains Client

The role is a **firmware choice**, not a runtime setting.

| Role | Receiver | Routes Zigbee traffic | Sleep/Poll Control | Current status |
|---|---|---|---|---|
| **Router** | Always on | **Yes** | No sleepy-device behavior | **Production / hardware validated** |
| **Mains Client** | **Always on** | **No** | **No sleep, no Poll Control** | Software + real-TC32 + reproducibility validated; **hardware canary pending** |

Mains Client deliberately uses Telink's end-device stack only for topology. It keeps `RxOnWhenIdle`, advertises mains power, does not enable `PM_ENABLE`, and removes the generic Poll Control behavior that belongs to ordinary EndDevice firmware.

The client candidate also hardens direct binding against the known very-short-press problem: fresh client configs use **RISE / press-start binding** and a **20 ms debounce** instead of depending on SHORT/release detection with the generic 50 ms debounce. Persisted user switch/binding settings still win.

Changing role requires another OTA because Router links `libzb_router` while Mains Client links `libzb_ed`. Cross-role OTA intentionally resets Zigbee network state and rejoins; application NVM is not deliberately cleared by that role-change path. Zigbee bindings are stack state, so they must be recreated after a role switch.

### How users will choose a role

For mixed homes, the cleanest distribution model is **one neutral normal index plus two temporary transition indexes**:

- `index_bseed.json` remains the normal BSEED OTA index. After Client promotion it will contain normal **Router→Router** and **Client→Client** updates plus the supported stock-conversion path, but **no role transitions**.
- `index_bseed_to_client.json` will be used temporarily when a user intentionally wants to change a Router into a **Mains Client**.
- `index_bseed_to_router.json` will be used temporarily when a user intentionally wants to change a Mains Client back into a **Router**.

After a role switch, rejoin/interview/reconfigure the device, recreate its direct Zigbee bindings, then restore `index_bseed.json`. This avoids persistent “wrong role available” notifications on other BSEED devices that are intentionally using a different role.

The two transition indexes will be published only after the Mains Client hardware canary is accepted. Until then, Mains Client images remain immutable GitHub Actions candidates rather than production OTA-index entries.

See [Router vs Mains Client](docs/bseed_roles.md) for the role contract, switching sequence, image identities and validation status.

### Supported stock conversion identities

| Stock Zigbee identity | Stock OTA image type | Conversion target |
|---|---:|---|
| `_TZ3000_b28wrpvx / TS011F` | `54179` | TS011F PM unified V8 |
| `_TZ3002_iedhxgyi / TS0726` | `54179` | TS0726 unified V8 |

The stock-facing wrappers use outer OTA version `0xFFFFFFFF`; after conversion, normal custom→custom updates use the target-specific custom image type and version.

## Quick start with Zigbee2MQTT

For production devices today, use the dedicated **Router** BSEED OTA index rather than historical generic fork entries:

```text
https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json
```

In Zigbee2MQTT, set it as the OTA override index and restart Zigbee2MQTT:

```yaml
ota:
  zigbee_ota_override_index_location: >-
    https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json
```

The production index currently contains exactly four manufacturer-specific paths: normal + stock-conversion OTA for each supported BSEED family.

Do not use the inherited generic `index_end_device.json` as a substitute for the BSEED Mains Client candidate. The BSEED client is an **always-awake mains leaf**, not the generic sleepy/power-saving EndDevice profile.

For complete update/conversion steps, see [Updating OTA](docs/updating.md), [Router vs Mains Client](docs/bseed_roles.md) and the [BSEED unified V8 guide](docs/bseed_unified_v8.md).

## Architecture

```mermaid
flowchart LR
    U[romasku upstream core] --> V[Unified V8 core]
    V --> PR[TS011F PM Router]
    V --> PC[TS011F PM Mains Client]
    V --> DR[TS0726 Router]
    V --> DC[TS0726 Mains Client]
    S1[Stock _TZ3000_b28wrpvx] -->|from-Tuya OTA wrapper| PR
    S2[Stock _TZ3002_iedhxgyi] -->|from-Tuya OTA wrapper| DR
    PR <-->|role transition OTA| PC
    DR <-->|role transition OTA| DC
```

Role-transition wrappers contain the compiled payload of the **destination role** while their outer OTA image identity matches the currently installed role. This keeps Router and Mains Client as distinct OTA identities without requiring programmer flashing to switch between them.

## Release integrity

Deployable BSEED firmware is produced by **GitHub Actions only**. Local compiler outputs are useful for diagnostics, but are not authoritative release candidates.

The production Router release path checks:

- host tests and lint;
- firmware image-type collision/identity rules;
- real pinned TC32 builds for both BSEED targets;
- exact OTA manufacturer/image/version headers;
- manifest provenance and clean-source state;
- a second PM build for byte-for-byte reproducibility;
- normal-vs-from-Tuya payload identity;
- generated OTA index consistency.

The Mains Client candidate additionally checks:

- the production Router binaries remain byte-identical;
- PM and TS0726 client images build with the real pinned TC32 toolchain;
- clients rebuild byte-for-byte identically;
- Router→Client and Client→Router wrappers have the expected OTA identities;
- receiver-on-when-idle, mains-power, no-sleep/no-Poll-Control and binding contracts remain intact.

The PM release `0x12053005` is intentionally reserved as a known-good recovery slot; normal development advanced past it to `0x12053006`.

## Safety

> [!CAUTION]
> Custom firmware can make a mains-powered device unusable. A successful stock→custom conversion does **not** prove that stock firmware can later be restored.

Before converting a stock device:

1. Match the **exact Zigbee manufacturer/model identity** shown above.
2. Do not infer compatibility from the enclosure or retail model name.
3. Treat conversion as potentially one-way unless you have a full original-firmware backup and a tested restore method for that exact hardware.
4. Keep power stable during OTA.

For a cross-role Router ↔ Mains Client OTA, enable **permit join before starting the update** because the role change resets Zigbee network state and expects the device to rejoin. Recreate direct Zigbee bindings after the device rejoins.

For the validated PM socket, the canonical custom configuration is:

```text
b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;
```

## Documentation

| Topic | Document |
|---|---|
| Router vs Mains Client, switching and validation | [BSEED firmware roles](docs/bseed_roles.md) |
| Architecture, identities, conversion and validation | [BSEED unified V8](docs/bseed_unified_v8.md) |
| OTA conversion and updates | [Updating OTA](docs/updating.md) |
| Supported hardware database | [Supported devices](docs/supported_devices.md) |
| Firmware changes | [Firmware changelog](docs/changelog_fw.md) |
| Known limitations | [Known issues](docs/known_issues.md) |
| Porting new hardware | [Porting guide](docs/contribute/porting.md) |

## Contributing and support

Contributions are welcome, especially when they keep hardware-specific behavior behind explicit target guards and preserve the shared upstream architecture where practical.

- [Contributing](CONTRIBUTING.md)
- [Support / bug-reporting guidance](SUPPORT.md)
- [Security policy](SECURITY.md)

For firmware changes, pull requests should keep deployable artifacts Actions-produced and preserve the normal CI + real-Telink validation boundary.

## Upstream and license

This project is a fork of [romasku/tuya-zigbee-switch](https://github.com/romasku/tuya-zigbee-switch). BSEED-specific changes are kept reviewable and compatible with the upstream architecture where practical.

See [LICENSE](LICENSE) for licensing terms.
