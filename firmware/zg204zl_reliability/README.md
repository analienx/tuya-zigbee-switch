# ZG-204ZL-3.0 / TLSR8258 VANT transmit power comparison

Development images for a one-sensor comparison. GitHub-hosted Actions verify the builds and image integrity. Battery-only boot, pairing and RF performance require hardware testing.

## Firmware policy

Both images use the radio's internal VANT supply. Flash **v0146 (+2.61 dBm)** first to isolate the radio change from v0145. If battery-only operation is confirmed, test **v0147 (+3.01 dBm)** on the same sensor and CR2450. Except for version identity, only the TX index differs between the images at source level. Both use the v0145 recovery schedule and retain pvvx PIR, illuminance, battery reporting, configurable `pir_timeout`, local lux gating, direct Zigbee On/Off binding and standard ZigbeeTLc OTA.

The only upstream is [pvvx/ZigbeeTLc at c249d8a7](https://github.com/pvvx/ZigbeeTLc/tree/c249d8a7caddb5012d06aba580d4a53d1957f9c1). Board 50 is ZG-204ZL-3.0 with TLSR8258F512ET32 and a CR2450. The overlay verifies all seven original source blob IDs before writing anything and refuses other boards at compile time.

| Setting | Upstream v0.1.4.0 | v0146 / v0147 |
|---|---|---|
| Manufacturer / image type | `0x1141 / 0x0232` | Preserved |
| File version | `0x01403001` | `0x01463001` / `0x01473001` |
| TX setting | Nominal +2.61 dBm, VANT | Nominal +2.61 / +3.01 dBm, VANT |
| Normal RX gain | Maximum | Preserved |
| Delays after accepted attempts | 30, 60, 120, 300, 600, 1200, 1800 s | **2, 3, 5, 10, 20, 40, 80, 300, 900, 1800 s**, then 1800 s |
| Channel masks | Known except every fourth request | **Known, known, full, full**, repeated |
| Security | Alternates | Alternates; both modes now cover both masks |
| SDK discovery scan attempts | 1 | 3 (not a claim that every secure rejoin scans three times) |
| Motion recovery | None | Bring the next retry forward to 100 ms once eligible |
| Motion budget | N/A | **Five minutes since any accepted automatic or motion request** |
| LED while unjoined | 50 ms flash about every 10 seconds while the stack is idle | Periodic flash removed |
| Motion and pairing LED | 50 ms motion flash and pairing feedback | Preserved |
| Debug printing | Disabled | Disabled; no new runtime diagnostics claimed |

An idle SDK receives the first lost-parent request after 100 ms if the timer is available. In the deterministic two-second busy model, the opening requests occur at approximately 0.1, 2.1, 5.1, 10.1, 20.1, 40.1, 80.1 and 160.1 seconds. Full-channel attempts use both security modes within the opening burst. Three consecutive busy or rejected callbacks wait one second each, followed by 5, 15, 60 and then 300 seconds for sustained refusal. The next accepted request clears this separate delay counter. Refusals leave the channel, security and accepted-request schedule unchanged.

If the first retry timer cannot be allocated, the app keeps the requested delay and retries allocation on its normal passes. While timer capacity is low it makes at most one allocation attempt per observed RTC second and adds no wake timer. Successful commissioning, restored connectivity and factory-new state clear the pending recovery.

During a long quiet outage the timer eventually settles at one automatic request per 30 minutes. Motion after at least five minutes since the last accepted request advances the owned retry to 100 ms without restarting the rapid ladder. Continuous movement cannot produce one attempt per minute. Movement inside the five-minute budget does not queue an automatic future attempt; another PIR rising edge is needed after eligibility returns. A button can explicitly restart rapid recovery while the SDK is idle.

Without motion, recovery after a long outage can wait up to the 30-minute timer; a changed channel can require later full-channel attempts. This is the deliberate battery-first tradeoff. Higher TX helps the sensor's uplink only; it does not increase router downlink power or override SDK parent ranking. These changes do not establish a fix for the observed initial node-descriptor interview failure.

## TX and battery limits

The pinned SDK uses VANT for the upstream +2.61 dBm setting and the highest VANT index, +3.01 dBm. The nominal 0.40 dB gain is about 10% more radiated power if the board and antenna reproduce the SDK values. Actual current and link margin require measurement. The previous v0145 +6.14 dBm index uses VBAT, whose nominal table Telink characterized at 3.3 V. [TLSR8258 datasheet, section 2.6.4](https://www.mouser.com/datasheet/2/1039/DS_TLSR8258_E_Datasheet_for_Telink_BLE_2bIEEE802_1-2301271.pdf)

Use +2.61 dBm as the reference. Keep the receiver, channel, position and orientation fixed. Compare cold starts, motion and direct binding, then measure rejoin after a parent outage. Repeat with a partly discharged CR2450. For power characterization, capture the supply at the chip and current during startup, first transmission and rejoin. [Coin-cell Zigbee measurements](https://www.nxp.com/docs/en/application-note/AN4573.pdf)

Characterize the actual CR2450 and board together. Continuous-drain ratings and pulse-test conditions describe different loads. [Panasonic CR2450 datasheet](https://energy.panasonic.com/dam/master/pdf/en/datasheet/lithium/CR2450_Datasheet_EN.pdf)

### Outage model and charge sensitivity

Hosted CI compiles the actual injected scheduler, plus the unchanged upstream scheduler. Its eight-hour model assumes a known channel, a full mask of channels 11..26, two seconds of SDK busy time per accepted request, no successful recovery and either no motion or one rising edge per minute. It records accepted requests and secure/insecure full-channel scans in `zg204zl-battery-model.json`. This is not an RF, association or measured-current model.

The quiet candidate is expected to issue **25 requests** versus upstream's **21**. The previous reviewed v0.1.4.1 model issued **54 quiet / 483 with minute-by-minute motion**, documented in [that hosted run](https://github.com/analienx/tuya-zigbee-switch/actions/runs/44632296442). The new five-minute shared floor bounds the moving scenario to at most 110 requests in the tested eight-hour case. The artifact contains its exact counts and charge calculations.

For sensitivity only, assume each old request uses 8 mA for one active second. Give each new request either 8 mA for one second or 16 mA for three seconds, to illustrate changes in both average active current and duration. The quiet 25-request case then adds **0.009–0.287 mAh per eight-hour outage**. Against an assumed healthy 20 microamp baseline (0.48 mAh/day), that is **about 2–60% extra daily charge** if this outage happens daily. These are assumptions, not measurements, confidence intervals or upper/lower bounds. Paired full-channel scans, unknown-channel operation and the real high-TX load can change the result substantially.

At the default 20-second poll interval, 4,320 daily polls with one assumed 18-byte on-air frame each at 250 kbps use **2.49 seconds of TX per day**. An assumed extra 10 mA during those frames adds `10 × 2.49 / 3600`, or **0.0069 mAh/day**. That is **1.4%** of the assumed 20 µA baseline. This example covers poll transmissions alone. Measure actual current, airtime and charge across motion reports, retries and outage scans before estimating battery life. [Board poll setting](https://github.com/pvvx/ZigbeeTLc/blob/c249d8a7caddb5012d06aba580d4a53d1957f9c1/src/board_zg204zl.h) · [Zigbee data rate](https://docs.silabs.com/zigbee/9.1.2/zigbee-mesh-network-performance/01-introduction-and-background)

Removing the unjoined blink saves about **0.048 mAh of LED charge over eight hours** if the device would otherwise flash for 50 ms every 10 seconds at 1.2 mA. Savings depend on time spent unjoined and actual LED current. The recovery simulation counts requests and does not include this LED estimate. [Board LED circuit](https://pvvx.github.io/ZG-204ZL-3.0/)

## Hosted build and review

The workflow [zg204zl-reliability-build.yml](../../.github/workflows/zg204zl-reliability-build.yml) pins the overlay to the exact PR head and the external source to `c249d8a7caddb5012d06aba580d4a53d1957f9c1`. It builds each variant in an independent job and verifies its embedded version.

The compiled harness covers delays and masks, both full-channel security modes, persistent SDK refusal, resuming after first timer allocation failure, success/factory-new cleanup, cooldown allocation failure, shared automatic/motion budget, unknown channel, button recovery, long sleep beyond the raw-clock wrap and the long-running attempt-counter wrap. It runs the section extracted from the patched source. CI stops if the overlay script returns an error. Image tests reject independent CRC, identity, payload, size and truncation corruption.

The target build runs the pinned TC32 toolchain and SDK. Native CRC, startup marker, length, version and OTA payload equality must pass, and native size must remain within the existing **0x20000** application erase footprint. Artifacts include:

- `ZG204ZL_v0146.bin` and `ZG204ZL_v0147.bin` for SWire.
- `1141-0232-01463001-ZG204ZL.zigbee` and `1141-0232-01473001-ZG204ZL.zigbee` for Zigbee OTA on already converted sensors.
- `manifest.json`: candidate source SHA, upstream pin, sizes and SHA-256 hashes.
- `source-overlay.json`, `sdk-toolchain-blobs.txt`, `zg204zl-battery-model.json`: source configuration, pinned dependencies and model evidence.

v0.1.4.5 supersedes v0.1.4.4 (`bf09bbc9`) with the retry fixes above. Keep the older artifacts for evidence.

## Physical canary

Confirm the exact target, actual installed build, flash preservation and recovery path before a separate canary. Stock Tuya `TS0601/_TZE200_3towulqd` has no OTA cluster `0x0019`; this standard ZigbeeTLc OTA is not a stock-conversion image. Never use the OTA container as the native SWire image.

For this sensor the owner selected one complete pre-write backup and one post-write comparison. Preserve every original backup and all identity/non-application sectors. A single read does not prove repeatability; gather stronger evidence if acquisition or device state is uncertain. BSEED-specific double-read/slot recovery rules do not automatically apply to this sensor. Never erase all flash.

Respect the 2.1 V rail and verify SWS voltage translation. See the [photo pinout and electrical notes](https://github.com/analienx/tuya-zigbee-switch/blob/653b054dfafc8cfc4611d67a6340c4dfb6c48078/docs/flashing/zg204zl-tlsr8253.md). Independently test high-TX voltage sag, boot/join, motion/lux reporting, direct binding, quiet and motion-assisted recovery, both channel/security paths, current/charge over an overnight outage and a partly discharged coin cell. Preserve the pinned v0.1.4.0 rollback with its own physical checks.

Private original files remain outside git under `C:\Workspace\scratch\zg204zl-hall-20261115\`. Download each reviewed artifact into a separate version-and-SHA directory after its exact-head hosted build succeeds. No current artifact is hardware approved by CI alone.
