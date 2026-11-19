# ZG-204ZL-3.0 Hall PIR — programming header, pinned firmware, and verification evidence

> **Status (2026-11-19):** Hardware pin orientation confirmed against the operator's four component-side PCB photographs and the pvvx board reference. The exact upstream ZigbeeTLc v0.1.4.0 native image is size/hash pinned. Two matching full **stock** flash reads exist. A later **first** post-flash read matches the new application image byte-for-byte, but the second post-flash read was interrupted, and some non-application flash sectors changed. **Do not call this a completed flash, network rejoin, calibration-preservation or Zigbee acceptance.** No device action is performed by this document.

## Target and physical identification

- Selected device: `HallPIRSensorEntrance`; original Zigbee IEEE **`0xa4c138adb5fa215f`**.
- Stock: `TS0601`, manufacturer `_TZE200_3towulqd`, Zigbee manufacturer code `0x1286`, application version 145.
- PCB silk-screen: **`ZG-204ZL-3.0`**.
- Chip **photographed on this board**: **`TLSR8258F512ET32`** (512 KiB). The pvvx hardware page and some comments say *TLSR8253*, but the actual silicon marking and pinned ZigbeeTLc build configuration support TLSR8258; do **not** use an 8253-only tool/profile.
- Board reference: https://pvvx.github.io/ZG-204ZL-3.0/ .
- Firmware board definition: https://github.com/pvvx/ZigbeeTLc/blob/c249d8a7caddb5012d06aba580d4a53d1957f9c1/src/board_zg204zl.h .

### Five-hole programming header: exact photograph orientation

**Look at the component/chip side. Keep the five programming holes along the TOP edge and the little push-button at UPPER RIGHT.** In that orientation, left to right:

```text
    PCB TOP EDGE / five contact holes
    ┌────────────────────────────────────────────────┐
    │   o         o         o         o         o    │
    │  [5]       [4]       [3]       [2]       [1]   │
    │ PB7/RX    PB1/TX    PA7/SWS     GND      +VBAT │
    │                                                │
    │                  TLSR8258 chip      [BUTTON]    │
    │                COMPONENT SIDE                  │
    └────────────────────────────────────────────────┘
```

| Left-to-right hole | Board pin number | Function | GPIO |
|---|---:|---|---|
| First (leftmost) | **5** | UART **RX** (board's RX) | PB7 |
| Second | **4** | UART **TX** (board's TX) | PB1 |
| Middle | **3** | **SWS / SWire debug-programming** | PA7 |
| Fourth | **2** | **GND** | Ground |
| Fifth (rightmost) | **1** | **+VBAT** (battery-input side) | Supply input |

**Reverse/solder-side view is MIRRORED.** These are PCB UART TX/RX designations, not the CP2102 TXD and RXD wiring for SWire.

For the qualified SWire setup the **three physical contacts to the board** are pin **3 SWS**, pin **2 GND**, pin **1 +VBAT**. Board UART TX/RX (pins 4/5) are not needed. A CP2102 *bidirectional* SWire adapter may internally use **TXD through 1.2 kΩ toward the RXD/SWS junction**, but **a 1.2 kΩ resistor does not level-shift a 3.3 V signal to a 2.1 V GPIO domain**. This circuit was validated on other 3.3 V Telink boards, not automatically on this PIR.

### Electrical stop conditions

- This PCB has an **XC6206 2.1 V regulator**. `+VBAT` is its **battery input**, **not** its 2.1 V output. Never inject 3.3 V into the regulator output.
- **Measure the actual I/O supply voltage before driving PA7/SWS** and use suitable level translation if SWS is powered at ~2.1 V. Do not connect bare 3.3 V CP2102 TXD to SWS. Check the exact TLSR8258 input maximum `V_IN <= VDD + 0.3 V`.
- Remove the coin cell before external bench power. Verify the external supply's voltage, polarity, load capability and GND continuity; **never apply 5 V**.
- A USB UART loopback, or TX↔RX echo, does **not** prove real target SWire responses.
- A working read is valuable evidence, but does **not** prove that prolonged overvoltage is safe.

## Exact verified upstream ZigbeeTLc firmware

**Native image for a qualified *wired* programmer (not an OTA container):**

| Field | Locked value |
|---|---|
| Upstream | `pvvx/ZigbeeTLc` |
| Immutable source commit | `c249d8a7caddb5012d06aba580d4a53d1957f9c1` |
| Release | `v0.1.4.0` |
| Board | `ZG-204ZL`, ID 50 |
| Native filename | **`ZG204ZL_v0140.bin`** |
| Native file size | **129140 bytes** |
| Native SHA-256 | **`792bb4452a249e1408e316d37a0daadf11626343f7f625ebaa9815497d04807a`** |
| Native image magic | `KNLT`, TLSR8258 `5d02` |
| Manufacturer / image type / version | `0x1141 / 0x0232 / 0x01403001` |
| Verified immutable upstream URL | https://raw.githubusercontent.com/pvvx/ZigbeeTLc/c249d8a7caddb5012d06aba580d4a53d1957f9c1/bin/ZG204ZL_v0140.bin |

**Never use the `.zigbee` OTA container as a SWire programming image.** Alternative OTA wrappers contain identical 129140-byte native payloads but different outer identities:

- Stock conversion candidate: `1286-0203-99993001-ZG204ZLz_v0140.zigbee` (manufacturer/type `0x1286/0x0203`).
- Future standard ZigbeeTLc OTA: `1141-0232-01403001-ZG204ZL.zigbee` (manufacturer/type `0x1141/0x0232`).

The **stock Hall endpoint did not advertise OTA-client cluster `0x0019`**, so normal stock-to-custom Zigbee OTA was not supported as observed on 2026-11-15. After a *verified* conversion, the custom firmware supports OTA and direct bound `genOnOff` commands. See the staged Home Assistant integration at https://github.com/analienx/home-assistant-stack/pull/104 and its pinned `home-assistant/zigbee2mqtt/zg204zl_ota.lock.json`.

**Private local files (not to be committed or copied to public Git):**

```text
C:\Workspace\scratch\zg204zl-hall-20261115\firmware\ZG204ZL_v0140.bin
C:\Workspace\scratch\zg204zl-hall-20261115\hall-stock-backup-retry1.bin
C:\Workspace\scratch\zg204zl-hall-20261115\hall-stock-backup-retry1.bin.second.bin
C:\Workspace\scratch\zg204zl-hall-20261115\hall-stock-backup-retry1.bin.json
C:\Workspace\scratch\zg204zl-hall-20261115\hall-postflash.bin
C:\Workspace\scratch\zg204zl-hall-20261115\hall-postflash.bin.json
```

Recheck the local native file without changing the sensor:

```powershell
python -c "import hashlib,pathlib; p=pathlib.Path(r'C:\Workspace\scratch\zg204zl-hall-20261115\firmware\ZG204ZL_v0140.bin'); b=p.read_bytes(); print(len(b),hashlib.sha256(b).hexdigest())"
```

The exact image can be fetched from the **immutable upstream URL above**, then checked against both size and SHA-256. Source/binary hash pinning is **not** equivalent to hardware-flash approval. Do not commit a duplicate binary; use the source URL and lock.

## Measured private read/flash evidence (2026-11-15)

1. **Two original stock reads:** `READ_MATCHED`, both 524288 bytes, same SHA-256:
   `bf9b35331591bbb8a01b6c5b6cd07fc0d667a2deb01fbc5b0f43fa8b4ef910c5`.
   The reader reported CPU control register `0x602 = 0x05` twice for each halt, demonstrating genuine bidirectional SWire responses.
2. **Pre-flash 4096-byte probe:** `READ_SINGLE`, successful and CPU halt verified.
3. **Later post-flash first read:** full 524288-byte file saved, SHA-256
   `577d4785d7c089e887d535e76364bc48aba4dbfa11ff37bc91878fba8e14103b`.
   On 2026-11-19, compared on the user machine: **the first 129140 bytes matched the pinned native image exactly (0 differences)**. This establishes evidence that the native application bytes reached flash; it is not proof of successful device boot.
4. **Post-flash second read:** interrupted; tool receipt `READ_FAILED / READ_INTERRUPTED`. Therefore **no `READ_MATCHED` post-flash result**.
5. **Outside the new image and its 0x20000 erase footprint**, 3600 bytes differed between the original stock backup and the first post-flash read:
   - Sector `0x34000`: 2 changed bytes
   - `0x35000`: 3314
   - `0x36000`: 57
   - `0x39000`: 195
   - `0x3D000`: 32

   These differences may include network/NVM changes, but their causes and consequences are **unproven**. Do **not** claim factory or IEEE data retention is fully verified. Keep both stock backups immutable. Do not erase these sectors, restore them wholesale, or edit identity/NVM without decoding their contents.

### Follow-up acceptance gates

- Verify a safe physical SWS voltage interface and the exact chip/flash geometry; no assumed 3.3 V GPIO compatibility.
- Obtain **two new matching full 512 KiB post-flash reads** and compare the image footprint plus a separately decoded NVM/identity map.
- Independently check retained IEEE identity and Zigbee network state. A restored or changed Zigbee network record can affect re-pairing.
- Check successful boot and real Zigbee2MQTT interview with PIR occupancy, lux, battery reporting, timeout and direct binding on a **noncritical** target.
- **The original `telink_swire_recovery.py flash` path is BSEED-specific** and its two-slot preservation layout is not automatically approved for the ZG-204ZL. Avoid generic/erase-all commands or repeating writes on uncertainty.
- The SWire human-mode `read --halt` recursion bug (first seen 2026-11-15) is corrected in https://github.com/analienx/tuya-zigbee-switch/pull/133 and the user's later installed helper reports source `58a5adab`; the fix is separate from this board's flash qualification.

**Related tracking:** [Hall OTA/source PR #104](https://github.com/analienx/home-assistant-stack/pull/104), [SWire inspection PR #132](https://github.com/analienx/tuya-zigbee-switch/pull/132), [Hall issue #102](https://github.com/analienx/home-assistant-stack/issues/102).
