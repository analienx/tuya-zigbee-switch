# TS0505B ZTU: Telink-native flash-image contract (research, NOT READY)

Scope: physically reported ZTU modules on three RGB+CCT bulbs. The module marking is stronger hardware evidence than the Zigbee model string, but each bulb's physical revision must be reconciled with its live OTA tuple before targeting it. A separate dismantled BZ2L/BL702C10 bulb is **not** part of this target.

## Established observations versus assumptions

- ZTU is a Tuya Zigbee Z2 module. Tuya lists its chip platform as TLSR8258; the module datasheet specifies 1 MiB flash, 64 KiB RAM, and a 3.3 V supply. That identifies the toolchain/ISA family, **not** the installed OEM bootloader or flash partitions.
- The stock Zigbee identity for the previously probed device is `TS0505B / _TZ3210_mja6r5ix`; OTA Query Next Image is `0x100B / 0x020C / 0x10003607`. These ZCL values do not independently identify the chip or its firmware payload format.
- Metadata-only offers with a fixed stock target/version/probe elicited block 0 at 65,536, 183,234, and 196,608 advertised bytes; 304,602 bytes did not elicit block 0 within 45 s. All block-0 attempts were aborted with `0x95`; **zero firmware payload bytes were sent**. A monotone hard size threshold is not proved.
- The 183,234-byte legacy image is an **EFR32MG21 Silicon Labs GBL**. Its successful metadata-only offer provides NO evidence of compatibility with ZTU. Never offer, stage, or flash that image on the ZTU target.
- A BP1633 marking was reported on a *different* dismantled bulb; it is not established as the driver fitted to the ZTU bulb. The exposed ZTU/LED board photograph does not establish driver pin mapping, polarity, or reset behavior.

## Source-backed hardware and development path

- Tuya ZTU reference: https://solution.tuya.com/hardware/detail/61005 and https://developer.tuya.com/en/docs/iot/zt3l-module-datasheet?id=Ka45nl4ywgabp
- ZTU pad-level reference: https://device.report/m/5d40c7fca2fff2c2d7a4b996ebfd5ad851e9a64044b31083ae759477f505d23b.pdf
- Telink Zigbee SDK manual and sampleLight_8258: https://doc.telink-semi.cn/doc/en/software/res/sdk/zigbee/zigbee_sdk_developer_manual_en/
- Tuya's Telink lighting application: https://developer.tuya.com/en/docs/iot-device-dev/tuyaos_zigbee_light_product_development_kit?id=Kd6efghkquo9d
- Existing Zigbee light/OTA code: https://github.com/doctor64/tuyaZigbee and https://github.com/pvvx/ZigbeeTLc . Audit license, SDK provenance, current OTA behavior, and target-specific code before reuse.

## OTA and flash format: separate three layers

1. **Zigbee envelope:** manufacturer `0x100B`, image type `0x020C`, version strictly above installed `0x10003607`, standard header, actual total size and full-image element. Preserve the stock OTA client's reported tuple; do not globally publish a colliding OTA index entry.
2. **Telink-native application:** TLSR8258/Z2 startup format, correctly linked execution address, embedded manufacturer/type/version fields at SDK-defined offsets, vendor-required CRC/checksum and valid length. The standard Telink tool converts its verified `.bin` into `.zigbee`; a GBL file is never an interchangeable payload. Validate output by independent parser and a known-good reference image.
3. **Installed OEM update policy:** identify actual Tuya boot/application variant, active slot, inactive OTA storage range, configuration/NV/MAC/UID partitions, optional encryption/authentication and any rollback/anti-clone logic. A block-0 request does NOT establish acceptance of layers 2 or 3. Do not infer OEM layout from a generic 512-KiB or 1-MiB SDK example.

Telink documents alternative boot arrangements: multi-address startup (including 0x00000 and 0x40000) versus a dedicated bootloader and application. Its **512-KiB Zigbee SDK example** reserves configuration/NV and specifies up to 208 KiB application size. This is compatible with the observed 192-KiB-versus-304,602-byte difference but does **not** prove the ZTU lamp has the same layout or limit: its module advertises 1 MiB flash. The stock implementation must be read or an exact OEM OTA image independently characterized.

**Immediate size criterion:** keep the first Telink-native research image at or below the observed 183,234-byte *outer Zigbee OTA* size where feasible, with real payload and all header/checksum overhead included. This is a conservative design target, not a proven permissible firmware-size limit; repeat the one-case zero-payload offer at the native candidate's **exact** size before any installation test.

## Firmware donor architecture and behavior

- Use the existing Telink SDK/toolchain and build/test infrastructure in the unified repository for platform, ZCL, OTA, storage and safe boot. Compare against `doctor64/tuyaZigbee` light implementation and `pvvx/ZigbeeTLc`; do not lift their compiled firmware or assume binary drop-in compatibility.
- Build `ZTU_TS0505B_RGB_CCT_ROUTER` as its own compile target. Zigbee endpoint 1: Home Automation / Extended Color Light; On/Off, Level, Color Control (CT, HS and XY), Identify, Groups, Scenes, OTA client. Preserve normal router behavior but do not enable an unnecessary concentrator or periodic many-to-one route requests.
- Board driver must be a separate all-off-at-startup module with individually verified channel mappings, output polarity, PWM timing, white-channel current limits and brownout behavior. The ZTU datasheet labels hardware PWM-capable pads C2, C3, D2, B4, B5; they are **candidates only**, not an assertion about these bulbs' seven or other solder joints.
- Implement version progression, reset/rejoin, NVM migration, binding/group restoration, and a second OTA update after first installation. Do not adopt external firmware with a known subsequent-OTA regression: `doctor64/tuyaZigbee` currently warns that an update can make the next OTA impossible, and its Tuya-to-custom instructions explicitly say TS0501B was not supported.

## Recovery and evidence acquisition — before any firmware bytes

1. On an unpowered sacrificial ZTU assembly, photograph BOTH boards and trace the exposed module contacts to LED drivers. Confirm whether the power stage is non-isolated before connecting any computer equipment. An assembled mains bulb is not a safe USB/SWS test target.
2. A qualified operator can isolate/remove the module, feed **only verified 3.3 V**, then use a Telink-compatible SWire programmer (not Silicon Labs SWD). The ZTU module datasheet lists SWS pad 4, GND pad 13, VCC pad 14 and reset pad 18; these pad numbers must be checked against the physical module orientation. Avoid any connection to mains, unisolated driver grounds, or charged capacitors.
3. Read JEDEC flash ID, chip revision and installed boot choice. Obtain complete per-unit flash backups twice through a tested read-only process, verify length and hashes, and store privately. Inspect candidate bootloader/start addresses, vendor CRC, embedded OTA identity, active/inactive slot, MAC/NV/calibration partitions and any UID-bound authorization **without rewriting them**. A readable chip ID alone does not prove firmware recovery.
4. Preserve a genuine *same-device, same-revision* OEM stock image and validate a recovery mechanism on an expendable isolated target before OTA installation. Existing TS0501B stock dumps are research examples only, not suitable rollback for TS0505B.
5. If firmware is inaccessible or its OEM validity mechanism cannot be satisfied, do not label OTA replacement flash-ready. A separate lab-only hardwired build can be used to validate LED behavior with a proven restoration path, but must never be published as an OTA update for stock bulbs.

A documented third-party approach uses a Telink SWire programmer and an offline full-flash read (https://github.com/doctor64/tuyaZigbee/blob/master/docs/flash.md). Its example application's `0x8000` address belongs to *that target*; it is **not** an established offset for these bulbs. No `erase`, `unlock`, `flash`, `write` or installation step is authorized by this research document.

## Release criteria for a single canary

- Positive per-unit ZTU/TLSR8258 identification, full same-target stock recovery and independently checked flash allocation (including untouched NVM, calibration and identity).
- A real Telink-native test image builds reproducibly under the pinned SDK; OTA wrapper identity, manufacturer/version, checksum, on-chip startup and active-bank rules are checked against *stock* firmware observations, not guessed from Silicon Labs GBL packaging.
- RGB+CCT driver is proven separately on safely isolated hardware, with five verified channels, current and brightness limits, hard all-off at reset and brownout, and an offline test matrix for HS/XY/CT/Level/OnOff.
- First and **second** OTA paths, Zigbee rejoin, bindings/groups and router behavior verified on a recoverable specimen. A forced power loss during staging must not destroy the working bank or identity store.
- Exact hash-pinned canary artifact, single-device private allowlist, activation-hold/replay controls and documented stop/recovery criteria. User approval for a live test is separate from a successful offline build.

**Status: NOT_READY.** Without a same-target stock dump/OEM image and a measured LED-driver map, the installed OTA acceptance policy and actual image layout cannot be closed through MQTT or generic module documentation alone.
