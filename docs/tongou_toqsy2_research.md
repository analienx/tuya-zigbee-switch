# Tongou TO-Q-SY2-163JZT: interoperability and flash-readiness dossier

Status: research / host-tested only. **Not approved for installation.**

## Scope and evidence levels

- Exact target: `TS011F`, `_TZ3000_cayepv1a`, retail model `TO-Q-SY2-163JZT`.
- Platform identified in prior board research: ZSU / EFR32MG21; BL0942 UART metering.
- Candidate application wiring (not hardware verified): PA5/PA6 BL0942 UART, PA4 relay, PD1 button, PC3/PC2 indicators. A second latching-relay drive and the temperature-sensor route are unresolved.
- The candidate firmware is a Zigbee client/application image. Neither compiling it nor producing a GBL proves that the stock bootloader will accept it.
- The device database marks this exact target `build: no` / `in_progress`. Keep it out of normal release and OTA override indexes.

## Compatibility inventory

| Surface | Stock behavior / evidence | Firmware-port status |
|---|---|---|
| Identity and topology | Router; endpoint 1 standard clusters; optional endpoint 242 Green Power | Exact Zigbee identity and descriptor need captured comparison |
| Relay | On/Off, power-on memory, countdown | Basic On/Off in generic core; physical relay and feedback unverified |
| Metering | Voltage/current/active power, cumulative energy | BL0942 driver and stock-like 0.01 kWh wire units host-tested; calibration needs hardware reference |
| Temperature | `msTemperatureMeasurement` and adjustable temperature cutoff | Sensor net, conversion curve, startup/failure behavior unknown |
| Thresholds | Current, voltage, power and temperature switches/limits | Public converter describes these; wire-level command/attribute map and actual hardware action unverified |
| Vendor clusters | `0xE000`, `0xE001` on stock endpoint 1 | Do not claim interoperability until read/write/report traces and cold-boot persistence pass |
| OTA | Stock endpoint advertises OTA client | Stock tuple, signing, slot map, backup and recovery not proven |

## Passive evidence collection (stock device, no actuator commands)

1. Record Basic cluster versions, exact manufacturer/model, endpoint descriptor, currently exposed thresholds and current/voltage/energy at rest. Do not change threshold values on an energized installation.
2. Capture existing Zigbee2MQTT traffic and correlate `0xE000`/`0xE001` attribute IDs, value types, manufacturer code, direction and reports with the published state. Separate observed traffic from converter assumptions.
3. Use normal OTA **check only** if needed to collect `{manufacturerCode, imageType, fileVersion}`. QueryNextImage proves a transport tuple, **not** acceptance of an unsigned GBL. Do not offer an image, start `update`, or set a broad OTA override.
4. Avoid changing global Zigbee2MQTT logging options/restarting the coordinator during routine capture. Existing `capture_tongou_ota_tuple.py` changes logging and can restart Z2M; do not run it unattended on a production mesh. Prefer a passive capture or an isolated laboratory mesh.
5. Save sanitized fixture data: remove site-specific names, IEEE addresses, network keys, MQTT credentials and unrelated devices before making fixtures public.

## Hardware questions that require a de-energized sacrificial sample

- Trace relay coil outputs and any relay auxiliary/contact-state feedback. Verify whether the contact is bistable and whether control is one pulse, two polarities or two distinct GPIOs. Never infer the second pin from board appearance alone.
- Establish whether thermal, overcurrent and voltage protections are independent hardware, a separate MCU, or implemented by the Zigbee SoC. Disabling stock firmware is unacceptable until this separation is known.
- Identify temperature sensor and calibrate it with controlled reference points; verify ADC curve and response to sensor open/short.
- Identify SWD access, flash protection, bootloader/storage partition layout, signing policy and a tested recovery mechanism. A memory dump may be unavailable when readout protection is set; do not erase the original sample to test this.
- Validate BL0942 wiring and calibration against an isolated, suitably rated test supply and reference meter. Firmware-side cutoff thresholds are **not** certified protective functions.

## Release gates, in order

1. **Software gate:** repeatable host tests for BL0942 framing, overflow, counter wrap/reset, meter units, startup state and malformed inputs; separate target-specific code from the established BSEED images.
2. **Protocol gate:** fixture-backed `0xE000/0xE001` read/write/report and restart-persistence comparison with the stock unit. Unsupported protection controls must be explicitly absent, not shown as functional placeholders.
3. **Hardware gate:** power-free continuity tracing plus isolated bench proof of relay pulse timing, actual contacts, thermal sensing and loss-of-comms behavior. Do not use an in-service breaker as the first test device.
4. **Safety gate:** establish certified protective mechanism independence or obtain qualified validation of the whole protection path. A generic application-level 63 A limit does not replace the breaker's original protection function.
5. **Boot/recovery gate:** determine stock bootloader's authentication/storage rules and prove read-back/restore on the *same* board revision. Reject stock-to-custom OTA packaging if signing/recovery are not demonstrably available.
6. **Canary gate:** only after all preceding gates pass, test the specific authorized spare unit on a noncritical isolated bench. Promote public firmware/release/index separately, with exact-hardware match and an explicit recovery plan.

## Reference material

- Zigbee2MQTT device capability reference: https://www.zigbee2mqtt.io/devices/TO-Q-SY2-163JZT.html
- ZHA stock signature and endpoint details: https://github.com/zigpy/zha-device-handlers/issues/3044
- Field observation of threshold defaults 75 V / 65 A: https://github.com/Koenkk/zigbee2mqtt/issues/32423
- Silicon Labs bootloader security configuration: https://docs.silabs.com/zigbee/latest/ota-bootload-server-client-setup-zigbee-sdk-v7x-higher/07-advanced-topics

**Scope boundary:** This repository does not assert compliance of modified firmware with the original breaker's electrical ratings or safety certification. No stock-to-custom OTA or energized protection test is authorized by this dossier.

## 2026-09-19 software iteration: concrete results and remaining blockers

- New pure C stock-wire codec: `src/base_components/tongou_threshold_codec.{c,h}`. E6 (`0x05` temperature / `0x07` power) and E7 (`0x01` current / `0x03` over-voltage / `0x04` under-voltage) carry four-byte `[selector, enable, uint16_be]` records. The `msg.data[2]` command byte described in Zigbee2MQTT is **outside** these records (ZCL framing).
- Decoder rejects truncated, duplicate, out-of-range, unknown-selector and non-binary-enable frames atomically. Encoder requires all applicable channel settings known before emitting a complete E6/E7 report and refuses a single update with an unknown companion setting. Its UI ranges are **protocol validation**, not certified trip characteristics.
- These are isolated pure functions: no NVM update, relay driver, OTA handling, Zigbee manufacturer identity, or protection action has been connected. Real stock report timing, rejoin seeding, relay semantics and durable storage remain open.
- `tests/test_tongou_threshold_codec.py` passes 15 standalone host tests; `tests/test_tongou_release_gate.py` passes 2 tests verifying `build: no`, null stock-facing OTA IDs and no entries in existing OTA indexes.
- BL0942 now uses 64-bit conversion intermediates, saturates 16-bit measurements and 32-bit cumulative energy, bounds the power calibration multiplier, refuses calibration without a fresh checksum-valid frame, and marks metering readings invalid after 5 s without a valid frame. Tests for initial/missing and stale calibration are added.
- The existing generic `electrical_measurement_cluster_update` retains old reporting attributes when data becomes invalid; `elec_meas_run_overload_protection` skips checks on invalid data. **This is not an acceptable Tongou fail-safe policy** and is not changed by the codec. Build a separate guarded protection model only after the independent-trip and physical relay path have been established.

### Native EFR32MG21 memory snapshot (previously built image)

`arm-none-eabi-size -A .../build/silabs/zigbee/build/release/zigbee.out` shows an explicitly reserved 4,096-byte stack, 22,904-byte ordinary `.bss`, 68,824-byte memory-manager heap region, 1,904-byte `.data`, 156-byte `.noinit`, 412-byte RAM code, and 4-byte boot reset section. The earlier combined BSS estimate of 95,984 bytes incorrectly treated the reserved heap as ordinary globals. These sections occupy the nominal 96 KiB SRAM without providing evidence of *runtime* high-water marks or dynamic allocation headroom. Do not assert that the firmware boots reliably merely because the linker succeeds; inspect/measure heap free blocks, stack high-water mark and allocation failures on an isolated development board.

### Captured stock OTA identity versus image acceptance

The earlier stock sample A Zigbee2MQTT `queryNextImageRequest` recorded manufacturer `4098/0x1002`, image type `5634/0x1602`, version `75`. A separate stock sample B debug capture timed out and did not provide an independent raw tuple. This identifies the sample A *query* only: it does **not** prove that the stock Tongou bootloader accepts an unsigned GBL, that other units share its bootloader revision, or that recovery works. Never enter this tuple into the firmware target or OTA distribution index before the separate recovery/signature gates pass.

### Offline capture recovery and regression status

- The historical sample A capture's raw Zigbee2MQTT message contains `(manufacturerCode=4098, imageType=5634, fileVersion=75)`, but the old helper's `ota_tuples` list was empty due an escaped-log parser bug. `helper_scripts/reparse_tongou_ota_capture.py` now reconstructs only the target-attributed tuple from the saved JSON without opening MQTT or altering Zigbee2MQTT; concurrent, unrelated OTA requests are ignored.
- The final focused regression set (`test_tongou_bl0942.py`, `test_tongou_threshold_codec.py`, `test_tongou_ota_probe.py`, `test_tongou_release_gate.py`, `test_boot_continuity.py`) passes 55/55 after rebuilding the default stub. A separate migration-only suite passes 44/44.
- The broader `pytest tests --maxfail=1` run **did not pass**: a migration test failed in combined ordering after ~one-third of the suite, although the failing case and its migration-only suite passed separately. The shared, repeatedly rebuilt stub binary/test isolation is a possible cause, but remains unproven. No full-suite-green or current EFR32-on-device claim is made.
- The local Tongou branch has not been merged or pushed. Keep unrelated README changes, case-colliding `README.md`/`readme.md`, local Python environments and generated files out of any future commit; review the exact staged changes before a source-only publication.

### 2026-09-19 follow-up validation and operational boundary

- The migration tests now build a private `build/stub/stub_device_migration` binary, rather than replacing the shared default simulator; the complete host suite passes **437/437** tests on the current research worktree. The earlier combined-order failure was a simulator-binary isolation problem, not a demonstrated migration firmware defect.
- The revised EFR32MG21 application compiled and linked in the isolated native SDK workspace. `.text` = 313040 bytes, `.bss` = 22908 bytes, reserved heap = 68816 bytes; these are linker allocations, **not** measured free runtime heap or a flashable-device acceptance proof. A follow-up rebuild after printf ABI fixes and a target-guarded BSEED variable completed without compiler warnings.
- The live-capture helper can change Zigbee2MQTT logging and restart it. It now refuses to run unless `--allow-live-network-changes` is explicitly supplied; default path is offline reconstruction through `reparse_tongou_ota_capture.py`. Do not run the live helper on the production network for this investigation.
- Confirmed outstanding hardware gates remain: latching-relay coil drive and feedback, temperature sensor and fault response, independent certified trip path, bootloader authentication and readback/recovery. No firmware image is approved for an energized breaker, regardless of host test/build results.
