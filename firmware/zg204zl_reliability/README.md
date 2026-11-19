# ZG-204ZL-3.0 / TLSR8258 — v0.1.4.1 reliability canary

**Development candidate. No target has been flashed, no HA/Z2M deployment has been made by this branch, and no hardware acceptance is implied by a green GitHub-hosted build.**

## Purpose

Make the already-supported pvvx direct Zigbee On/Off binding more resilient to lost parents and lengthy coordinator outages. Keep the PIR occupancy, illuminance, local lux gating, battery reporting, configurable `pir_timeout`, Zigbee identity and standard ZigbeeTLc OTA support.

Based exclusively on immutable **[pvvx/ZigbeeTLc@c249d8a7](https://github.com/pvvx/ZigbeeTLc/tree/c249d8a7caddb5012d06aba580d4a53d1957f9c1)**, using a minimal overlay applied during a public GitHub Actions run. No proprietary stock-firmware bytes or full-flash household backups are committed.

| Item | Upstream v0.1.4.0 | Candidate v0.1.4.1 |
|---|---|---|
| Board | ZG-204ZL board 50 | Same |
| Chip | TLSR8258F512ET32 | Same |
| Manufacturer / image type | `0x1141 / 0x0232` | Same |
| File version | `0x01403001` | **`0x01413001`**, higher than upstream |
| Rejoin delays | 30 s, 60 s, 2, 5, 10, 20, 30 min | **5, 10, 20, 45, 90, 180, 300, 600 s** |
| Channel scanning | Full channel every fourth rejoin request | **Preserved** (known channel preferred otherwise) |
| SDK discovery scan attempt count | 1 | **3** |
| Activity-based retry | Button click triggers immediate rejoin | Motion may advance one pending retry per 60 seconds, preserving its backoff and channel progression; requests coalesce while the SDK is busy |
| Transmit power | Nominal **+2.61 dBm** on CR2450 | **+3.01 dBm** (supported next step; +0.40 dB) |
| RX sensitivity | Already maximum RX gain during normal operation | **Unchanged**; active discovery uses automatic gain |
| Diagnostics | Optional debug printing, disabled in normal builds | Still disabled; no new runtime counters or Zigbee attributes claimed |
| Direct binding, lux, battery | Included | Preserved |

### Important limitations

- The TX difference is just **0.4 dB** (~10% radio power), not an RF range guarantee. Actual coin-cell voltage and RF output must be measured. The existing RX path already uses maximum gain for normal receiving; forcing maximum during active scanning can harm gain/ranking fidelity, so RX is not changed.
- The higher scan-attempt count and shorter retry schedule can increase battery usage during extended nightly coordinator outages. PIR motion while disconnected triggers an extra attempt **at most once every 60 seconds**. Recovery behavior must be measured against a v0.1.4.0 baseline with the same battery and nearby routers.
- The Zigbee stack retains authority over parent candidate ranking; this does **not** add a proprietary parent blacklist or guarantee choice of the strongest router.
- Debug printing is disabled in the actual canary. Neither SWire messages nor new Zigbee diagnostics should be expected from this artifact.
- The PCB has a regulated **2.1 V logic rail**; an ordinary 3.3 V UART TXD is **not proven safe** on PA7/SWS. See [the photo pinout and electrical notes](https://github.com/analienx/tuya-zigbee-switch/blob/653b054dfafc8cfc4611d67a6340c4dfb6c48078/docs/flashing/zg204zl-tlsr8253.md).
- Earlier Hall factory readback was `READ_MATCHED` twice; later first post-flash full read matched the upstream v0.1.4.0 application but a second read was interrupted, and private NVM-sector differences were found. These unresolved findings remain before any new wired flash.

## Hosted CI build procedure

The workflow [`zg204zl-reliability-build.yml`](../../.github/workflows/zg204zl-reliability-build.yml):

1. Checks out exactly pvvx/ZigbeeTLc `c249d8a7caddb5012d06aba580d4a53d1957f9c1`.
2. Pins and verifies the exact candidate head independently of that external checkout. Runs Python tests and a compiled C timer/request harness on **GitHub-hosted** runners only.
3. Verifies Git blob SHA-1 of six specific upstream files, then patches the checked-out source; mismatched sources cause **no files to be written**.
4. Builds just `BOARD_ZG204ZL` using the upstream Zigbee SDK and Telink TC32 toolchain, with a bump to `APP_BUILD=0x41`.
5. Verifies native CRC, header, version, OTA wrapper identity and identical payload, and refuses firmware larger than the previous **0x20000** application erase footprint.
6. Uploads `ZG204ZL_v0141.bin` (raw native SWire image), `1141-0232-01413001-ZG204ZL.zigbee` (post-conversion Zigbee OTA), overlay provenance, candidate SHA, pinned SDK/toolchain blob IDs, battery request-count model and manifest as a **candidate** artifact.

The harness compiles the exact recovery section produced by the overlay. It
checks accepted/rejected/busy SDK calls, timer ownership, channel fallback with
repeated motion, successful recovery, factory-new exclusion, timer exhaustion,
and cooldown after long sleeps beyond a raw-clock wrap. The unchanged upstream
scheduler is compiled separately to compare request counts. This is not an RF,
association, security-exchange, battery-current or hardware acceptance model.

## Battery estimate and review corrections

The original v0.1.4.1 artifact from source `526bd7e5` is superseded by the review
fixes; do not deploy its native SHA `9de5517bc3a4362b2f8f1d1b3b168c7fcd8d5951ca2a28e348d544b73be29e26`.
Motion previously reset the entire fast ladder and could starve full-channel
fallback. The corrected path preserves progression, uses an owned 60-second SDK
timer instead of the wrapping 16 MHz clock, and never issues a rejoin while the
SDK reports another attempt active. Only accepted requests advance security,
channel and backoff state. Lost-parent entry uses that same owned scheduler.

An eight-hour, no-success outage model uses a known channel, all-channel mask
11..26, and two seconds of simulated SDK busy time per accepted request. The
upstream schedule makes 21 requests; the quiet candidate makes 54. Mature quiet
retry frequency is approximately 2/hour versus 6/hour. Repeated motion may advance
one request per minute, so a busy room during a long outage costs substantially
more than a quiet room. Exact compiled-harness counts are in the artifact's
`zg204zl-battery-model.json`; rejected/busy calls do not count as radio attempts.

For a planning example only, assume 8 mA active current and one active second
per old request. If a new request takes one to three active seconds, the quiet
candidate adds **0.07..0.31 mAh per eight-hour outage**. With an assumed 20 uA
healthy baseline (0.48 mAh/day), that is **15..65% extra daily charge** when such
an outage occurs every day. This is not a measured range, upper bound or battery
life prediction: actual scanning masks/duration, security waits, current and
motion can dominate. `ZDO_NWK_SCAN_ATTEMPTS` configures network discovery; it
does not prove that every secure rejoin performs three scans. The three-second
case is a cost sensitivity assumption, not a measured consequence of that macro.

Connected normally, retry changes are inactive. The 0.4 dB TX step is about
9.6% more radiated power, not 9.6% more electrical current. For illustration,
an extra 0.5 mA during ten transmit seconds per day adds 0.0014 mAh/day, about
0.3% of that assumed baseline. Actual TX current/duty and overnight outage
charge must be measured before claiming a battery-life improvement or penalty.

Do not publish, flash, rename, change manufacturer/type, or add this OTA to a shared network index before examining the GitHub Actions logs and exact artifact hashes.

## Physical canary plan

- Confirm that the same device is running pvvx/ZigbeeTLc and its actual firmware identity before choosing OTA. **Stock Tuya `TS0601/_TZE200_3towulqd` does not advertise OTA cluster `0x0019`; this new standard ZigbeeTLc OTA is not a stock-conversion image.**
- Preserve all original backups. For this sensor's separately authorized launcher the owner selected one complete pre-write backup and one post-write comparison; a single read does not establish repeatability. This does not relax BSEED double-read recovery gates. Collect stronger evidence when an existing acquisition or the actual device state remains uncertain.
- Verify 2.1 V logic / SWS voltage translation and preserve the identity and non-application flash sectors. Do not erase all flash or reuse a BSEED socket-specific slot strategy.
- On a noncritical target, compare old vs new: successful association, chosen parent, LQI/RSSI under the same physical conditions, time to recover after router unplug/replug, time to recover after coordinator outage, battery consumption over at least an overnight outage, reliable motion and direct-bound On/Off.
- If recovery or battery life regresses, keep the original pinned v0.1.4.0 candidate for rollback, with its own independent safety/flash checks.

### Paths on workstation

Existing **private** original files (not in repository): `C:\Workspace\scratch\zg204zl-hall-20261115\`.

The build-generated artifact should be downloaded only from the workflow run tied to this branch's exact SHA, after CI succeeded. **Never substitute an upstream OTA container for the raw SWire `.bin`.**
