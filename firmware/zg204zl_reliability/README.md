# ZG-204ZL-3.0 / TLSR8258 — v0.1.4.2 high-TX battery-first canary

Development candidate for one hardware canary. Builds and tests run only on GitHub-hosted Actions. This branch has not flashed a sensor, changed Home Assistant or published an OTA index. A successful build establishes image integrity, not boot, pairing, battery life or RF acceptance.

## Firmware policy

The owner requested a substantial TX increase and selected **battery first: back off longer and retry promptly on motion**. This candidate retains pvvx PIR, illuminance, battery reporting, configurable `pir_timeout`, local lux gating, direct Zigbee On/Off binding and standard ZigbeeTLc OTA.

The only upstream is [pvvx/ZigbeeTLc at c249d8a7](https://github.com/pvvx/ZigbeeTLc/tree/c249d8a7caddb5012d06aba580d4a53d1957f9c1). Board 50 is ZG-204ZL-3.0 with TLSR8258F512ET32 and a CR2450. The overlay verifies all six original source blob IDs before writing anything and refuses other boards at compile time.

| Setting | Upstream v0.1.4.0 | New v0.1.4.2 |
|---|---|---|
| Manufacturer / image type | `0x1141 / 0x0232` | Preserved |
| File version | `0x01403001` | `0x01423001` |
| TX setting | Nominal +2.61 dBm, VANT | SDK maximum, nominal **+10.46 dBm**, VBAT |
| Normal RX gain | Maximum | Preserved |
| Delays after accepted attempts | 30, 60, 120, 300, 600, 1200, 1800 s | **2, 3, 5, 10, 20, 40, 80, 300, 900, 1800 s**, then 1800 s |
| Channel masks | Known except every fourth request | **Known, known, full, full**, repeated |
| Security | Alternates | Alternates; both modes now cover both masks |
| SDK discovery scan attempts | 1 | 3 (not a claim that every secure rejoin scans three times) |
| Motion recovery | None | Bring the next retry forward to 100 ms once eligible |
| Motion budget | N/A | **Five minutes since any accepted automatic or motion request** |
| Debug printing | Disabled | Disabled; no new runtime diagnostics claimed |

An idle SDK receives the first lost-parent request after 100 ms. In the deterministic two-second busy model, the opening requests occur at approximately 0.1, 2.1, 5.1, 10.1, 20.1, 40.1, 80.1 and 160.1 seconds. Full-channel attempts use both security modes within the opening burst. Actual scans/security exchanges can take longer: busy or rejected requests wait one second and do not advance channel, security or backoff progression.

During a long quiet outage the timer eventually settles at one automatic request per 30 minutes. Motion after at least five minutes since the last accepted request advances the owned retry to 100 ms without restarting the rapid ladder. Continuous movement cannot produce one attempt per minute. Movement inside the five-minute budget does not queue an automatic future attempt; another PIR rising edge is needed after eligibility returns. A button can explicitly restart rapid recovery while the SDK is idle.

Without motion, recovery after a long outage can wait up to the 30-minute timer; a changed channel can require later full-channel attempts. This is the deliberate battery-first tradeoff. Higher TX helps the sensor's uplink only; it does not increase router downlink power or override SDK parent ranking. These changes do not establish a fix for the observed initial node-descriptor interview failure.

## TX and battery limits

The pinned SDK's `platform/chip_8258/rf_drv.h` labels `RF_POWER_INDEX_P10p46dBm` as VBAT power and the old +2.61 dBm as VANT. This changes the PA supply mode as well as the index. [Telink's RF guide](https://wiki.telink-semi.cn/tools_and_sdk/Driver/doc/kite/html/md__project_1__table_of__content_14__t_s_i__r_f__features.html) specifies a maximum near +10 dBm; the [TLSR8258 datasheet, section 2.6.4](https://wiki.telink-semi.cn/doc/ds/DS_TLSR8258-E_Datasheet%20for%20Telink%20BLE%2BIEEE802.15.4%20Multi-Standard%20Wireless%20SoC%20TLSR8258.pdf) explains that VBAT output depends on supply voltage and falls below its nominal output below 3 V.

**This PCB has a regulated 2.1 V logic rail. Neither +10.46 dBm radiated output nor TX pulse current is proven on this board.** The +7.85 dB nominal step corresponds to about 6.1 times radiated power under the SDK reference conditions, not 6.1 times range or battery current. Coin-cell/board voltage sag can erase the benefit or cause resets. Measure the actual rail during high-power transmissions on a fresh and partly discharged battery before hardware acceptance. Do not bypass the regulator or raise the supply as part of this source change.

The [Panasonic CR2450 sheet](https://energy.panasonic.com/dam/master/pdf/en/datasheet/lithium/CR2450_Datasheet_EN.pdf) characterizes its own cell at low continuous drain. It does not qualify this unknown cell, board regulator or high-TX pulse load. Continuous-drain figures are not hard pulse-current limits.

### Outage model and charge sensitivity

Hosted CI compiles the actual injected scheduler, plus the unchanged upstream scheduler. Its eight-hour model assumes a known channel, a full mask of channels 11..26, two seconds of SDK busy time per accepted request, no successful recovery and either no motion or one rising edge per minute. It records accepted requests and secure/insecure full-channel scans in `zg204zl-battery-model.json`. This is not an RF, association or measured-current model.

The quiet candidate is expected to issue **25 requests** versus upstream's **21**. The previous reviewed v0.1.4.1 model issued **54 quiet / 483 with minute-by-minute motion**, documented in [that hosted run](https://github.com/analienx/tuya-zigbee-switch/actions/runs/44632296442). The new five-minute shared floor bounds the moving scenario to at most 110 requests in the tested eight-hour case. The artifact contains its exact counts and charge calculations.

For sensitivity only, assume each old request uses 8 mA for one active second. Give each new request either 8 mA for one second or 16 mA for three seconds, to illustrate changes in both average active current and duration. The quiet 25-request case then adds **0.009–0.287 mAh per eight-hour outage**. Against an assumed healthy 20 microamp baseline (0.48 mAh/day), that is **about 2–60% extra daily charge** if this outage happens daily. These are assumptions, not measurements, confidence intervals or upper/lower bounds. Paired full-channel scans, unknown-channel operation and the real high-TX load can change the result substantially.

When connected normally, recovery retries stop. Higher TX still affects ordinary traffic. For example, an assumed extra 10 mA during 1–10 transmit seconds per day adds **0.0028–0.0278 mAh/day**, or about **0.6–5.8%** of the same assumed baseline. Measure actual TX current and airtime before using either example as a battery-life prediction.

## Hosted build and review

The workflow [zg204zl-reliability-build.yml](../../.github/workflows/zg204zl-reliability-build.yml) independently pins the overlay to the exact PR head and the external source to `c249d8a7caddb5012d06aba580d4a53d1957f9c1`. The branch name retains its original v0141 label; artifact names and embedded version identify this v0142 candidate.

The compiled harness covers delays and masks, both full-channel security modes, busy/rejected calls, success/factory-new cleanup, cooldown allocation failure, shared automatic/motion budget, unknown channel, button recovery, long sleep beyond the raw-clock wrap and the long-running attempt-counter wrap. It tests the section extracted from the patched source, not a separate Python scheduler. Image tests reject independent CRC, identity, payload, size and truncation corruption.

The target build runs the pinned TC32 toolchain and SDK. Native CRC, startup marker, length, version and OTA payload equality must pass, and native size must remain within the existing **0x20000** application erase footprint. Artifacts include:

- `ZG204ZL_v0142.bin`: native SWire image.
- `1141-0232-01423001-ZG204ZL.zigbee`: standard Zigbee OTA after conversion.
- `manifest.json`: candidate source SHA, upstream pin, sizes and SHA-256 hashes.
- `source-overlay.json`, `sdk-toolchain-blobs.txt`, `zg204zl-battery-model.json`: source configuration, pinned dependencies and model evidence.

The earlier v0.1.4.1 artifacts, both original source `526bd7e5` and reviewed source `fcb1447a`, are superseded for the owner's newly requested policy. Keep them immutable for evidence. The new embedded identity is higher to distinguish this policy and power change. No firmware bytes or household backups are committed.

## Physical canary

Confirm the exact target, actual installed build, flash preservation and recovery path before a separate canary. Stock Tuya `TS0601/_TZE200_3towulqd` has no OTA cluster `0x0019`; this standard ZigbeeTLc OTA is not a stock-conversion image. Never use the OTA container as the native SWire image.

For this sensor the owner selected one complete pre-write backup and one post-write comparison. Preserve every original backup and all identity/non-application sectors. A single read does not prove repeatability; gather stronger evidence if acquisition or device state is uncertain. BSEED-specific double-read/slot recovery rules do not automatically apply to this sensor. Never erase all flash.

Respect the 2.1 V rail and verify SWS voltage translation. See the [photo pinout and electrical notes](https://github.com/analienx/tuya-zigbee-switch/blob/653b054dfafc8cfc4611d67a6340c4dfb6c48078/docs/flashing/zg204zl-tlsr8253.md). Independently test high-TX voltage sag, boot/join, motion/lux reporting, direct binding, quiet and motion-assisted recovery, both channel/security paths, current/charge over an overnight outage and a partly discharged coin cell. Preserve the pinned v0.1.4.0 rollback with its own physical checks.

Private original files remain outside git under `C:\Workspace\scratch\zg204zl-hall-20261115\`. Download each reviewed artifact into a separate version-and-SHA directory after its exact-head hosted build succeeds. No current artifact is hardware approved by CI alone.
