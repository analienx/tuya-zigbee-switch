# BSEED Zigbee Firmware

**Reliable, hardware-specific firmware for BSEED sockets and switches—built on [Romasku's Tuya Zigbee Switch](https://github.com/romasku/tuya-zigbee-switch).**

Exact device identity. Independent relay control. PM telemetry. Router and mains-Client recovery. Reproducible firmware releases, with hardware acceptance kept distinct from a successful build or OTA transfer.

[**Get started**](#getting-started) · [**Firmware availability**](docs/bseed_socket_release_status.md) · [**Release notes**](docs/releases/README.md) · [**Documentation**](docs/README.md) · [**Contribute**](CONTRIBUTING.md)

[![Tests](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml)
[![PM role matrix](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml)
[![Telink Router TC32](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/telink-router-ci.yml/badge.svg)](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/telink-router-ci.yml)

> [!CAUTION]
> **Mains-powered hardware and potentially irreversible firmware changes.** Never identify a device by its enclosure or generic `TS011F` label. Check the exact manufacturer/model, board and OTA image type. An image that compiles—or even transfers successfully—can still leave a device unusable. Read [update and recovery guidance](docs/updating.md) before attempting an OTA.

> **BSEED operator automation:** Device-loss risk is warned about here and is covered by the current operator's [standing, device-wide acknowledgement](docs/bseed_ota_operator_authorization.md). Automation must **not repeatedly demand the same risk sentence** before each OTA. This does not waive firmware integrity, electrical safety, Zigbee health or exact hardware checks, and it does not mean other repository users have consented.

## Start with the right guide

| I want to… | Open |
| --- | --- |
| Check whether my firmware is published or experimental | [Firmware availability](docs/bseed_socket_release_status.md) |
| Install an accepted public Router image | [Zigbee2MQTT update guide](docs/updating.md) |
| Understand the latest stability improvements | [2026-11-02 candidate release notes](docs/releases/2026-11-02-link-policy-candidates.md) |
| Run a controlled canary | [Single-device OTA runner](docs/bseed_targeted_ota_runner.md) |
| Understand the firmware and release architecture | [Documentation hub](docs/README.md) |

## Firmware availability

**Snapshot: 2026-11-05.** The public BSEED OTA index contains accepted **Router** images and matching stock-conversion wrappers. The newer draft candidates are unsealed and unpublished. Each device family has its own native OTA identity.

| Hardware family | Zigbee manufacturer / model | Published Router | Current sealed Router / mains Client |
| --- | --- | --- | --- |
| TS011F-BS-PM · power monitoring | `b28wrpvx` / `TS011F-BS-PM` | `1.2.5-bseedv8u4` | `1.2.5-bseedr20` / `1.2.5-bseedcli23` |
| TS011F-BS · no metering | `o1jzcxou` / `TS011F-BS` | `1.1.3-bseedv8` | `1.1.3-bseedr19` / `1.1.3-bseedc17` |
| TS0726 · three-gang switch/dimmer | `iedhxgyi` / `TS0726-3-BS` | `1.1.8-bseedv8` | Separately controlled TS0726 candidate train |

**Do not mistake the rightmost column for an upgrade offer.** These new identities are unsealed draft candidates; their implementation and hardware acceptance are pending. Client images and role-transition wrappers are not in the public index. [See full identities, hex versions and acceptance status](docs/bseed_socket_release_status.md).

## What this fork adds

| Area | What it provides |
| --- | --- |
| **Power monitoring** | BSEED PM hardware integration for voltage, current, active power and cumulative energy, with device-specific scaling and reporting gates |
| **Relay and button independence** | Configure logical Zigbee behavior separately from the physical relay, supporting smart-bulb and detached-switch use cases |
| **Router resilience** | Telink recovery and child-parenting hardening, including a historically tested IKEA RODRET sleepy-child canary |
| **Mains-Client recovery** | Failure-driven parent rejoin and rescan logic; no generic factory reset or forced reboot on every lost message |
| **Safe maintenance** | Unique OTA image types, immutable firmware identity, GitHub-hosted builds and gated single-target update tools |

The newest parent/route policy uses measured failure and cooldowns rather than changing parents at every weak signal. [Read the design](docs/bseed_link_policy.md). A green CI run never substitutes for on-device testing.

## Getting started

1. **Identify your exact device.** Match its Zigbee manufacturer, model, board and intended Router/Client role in the [firmware status table](docs/bseed_socket_release_status.md).
2. **Review the risks.** Stock-to-custom conversion may not be reversible without an exact-board firmware backup and tested recovery method. Read [updating](docs/updating.md) and [known issues](docs/known_issues.md).
3. **For an explicitly supported public image**, configure Zigbee2MQTT with the BSEED override index:

   ```yaml
   ota:
     zigbee_ota_override_index_location: >-
       https://raw.githubusercontent.com/analienx/tuya-zigbee-switch/main/zigbee2mqtt/ota/index_bseed.json
   ```

4. **Check the offered image before transfer.** The normal index contains accepted Router/stock-conversion entries only; do not use it to infer Client availability. Apply configuration changes and restart Zigbee2MQTT only when no OTA or coordinator maintenance is in progress.
5. **Verify after updating.** Reinterview and confirm the actual build, role, relay policy, metering, bindings and mesh behavior. OTA `status: ok` is not final hardware acceptance.

[Full installation instructions](docs/updating.md) · [Targeted canary runbook](docs/bseed_targeted_ota_runner.md)

## Firmware release discipline

**Source → exact-SHA GitHub-hosted CI → sealed artifact → one-device canary → hardware acceptance → public OTA publication.**

A native PM Router image cannot be substituted for the Client image, even when both share the same numerical version. Identical-looking TS011F enclosures may contain different boards. Every promotion must preserve the firmware identity registry and use verified artifact bytes; historical seals are immutable.

[Release-process guide](docs/contribute/release.md) · [Evidence-led release notes](docs/releases/README.md) · [PM role matrix](docs/bseed_pm_variant_matrix.md)

## Project documentation

The [documentation hub](docs/README.md) groups setup, OTA operations, hardware validation, architecture and contributing guides. For broader upstream devices, use the [supported-device database](docs/supported_devices.md) and [porting guide](docs/contribute/porting.md); entries there are not blanket authorization for BSEED-specific images.

This fork preserves [upstream attribution](https://github.com/romasku/tuya-zigbee-switch), architecture and licensing. See [LICENSE](LICENSE), [SECURITY.md](SECURITY.md) and [SUPPORT.md](SUPPORT.md).
