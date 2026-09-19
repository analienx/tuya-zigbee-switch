# TS0505B / `_TZ3210_mja6r5ix` RGB+CCT light

Experimental Silicon Labs target for the Tuya `TS0505B` extended-color light family.

This directory contains reusable firmware and OTA-transport work only. It intentionally excludes household device names, IEEE addresses, room names, local filesystem paths, and per-home Zigbee telemetry.

## Stock fingerprint

- model: `TS0505B`
- manufacturer: `_TZ3210_mja6r5ix`
- logical type: Router
- endpoint 1: HA profile `0x0104`, Extended Color Light `0x010D`
- stock Basic identity: app `112`, stack `2`, hardware `0`, software build `z.1.0`
- OTA Query Next Image tuple, independently observed on multiple matching stock units: `0x100B / 0x020C / 0x10003607`

## Implemented

- board-neutral RGB+CCT logical state core for On/Off, Level, CT, HS and XY
- exact endpoint/fingerprint profile
- conservative router-table profile
- Tuya ZSU family reference pin profile
- reproducible Silicon Labs reference build manifests
- dark D0 transport-candidate metadata
- metadata-only Zigbee OTA acceptance probe under `zigbee2mqtt/extensions/`
The physical-output hook remains intentionally inert until production-board polarity, PWM frequency and safe reset levels are independently proven.

## Historical full-size OTA blocker

A stock client was offered a structurally valid custom image with a newer outer Zigbee OTA version. It completed Image Notify / Query Next Image exchange but did not request image block 0. Post-attempt OTA attributes remained idle and no candidate bytes were staged.

Tuya documents firmware ceilings of 376 KiB for EFR32MG21A020F768 and 528 KiB for EFR32MG21A020F1024. The D0 image is 304,602 bytes, so the documented 768-KiB firmware-size ceiling alone does not explain the refusal.

Use the metadata-only acceptance probe to vary **only** Query Next Image response metadata and abort on the first block request. This separates version/size acceptance from GBL contents without sending firmware payload bytes.

See [OTA transport research](docs/ota-transport.md).

## Offline preflash gate

See [preflash contract](docs/preflash.md). The currently frozen D0 is a dark diagnostic artifact, not a lighting-capable production image; the fail-closed release gate remains red.

## Compact candidate: transport entry demonstrated

The [slim reference project](firmware/slim_reference/) builds an 183,234-byte
experimental full-image OTA for both MG21 flash-density templates. A stock
client requested block 0 when this exact size was offered by the metadata-only
probe; the probe aborted and no firmware payload bytes were sent. See the
[anonymized evidence](evidence/ota-prebyte-size183234-20260919.json). The
[offline build recipe](../../helper_scripts/ts0505b/build_slim.py) produces
variant-specific hashes and marks both builds *not deployable*.

**Do not flash:** Installed silicon density, installed bootloader/rollback
policy, verified physical RGB+CCT output and recovery remain unproven.

## Physical board and recovery evidence

The generic kit PWM pin example conflicts with the ZSU datasheet on PD02; see [hardware identification and recovery](docs/hardware-identification.md). No debug probe was connected during the research, and reference pad names do not establish actual production wiring. Keep the output and flash-readiness gates closed until physical evidence is available.
