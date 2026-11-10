# Wired recovery over SWire

Use this when a Telink TLSR8258 device no longer starts or cannot take an OTA
update. It rewrites image slot A with a USB-UART adapter. The Zigbee network
record, IEEE address, factory calibration and energy total stay on the chip,
so a recovered device rejoins its network without re-pairing.

The tool is [`helper_scripts/telink_swire_recovery.py`](../../helper_scripts/telink_swire_recovery.py).
It is one file that needs Python 3.8 or newer and `pyserial`.

> [!IMPORTANT]
> The board must be disconnected from mains for the whole procedure.
> Power the module only from the adapter's 3.3 V or a separate 3.3 V supply,
> never from mains and never from 5 V.

## What you need

- A USB-UART adapter that can put out 3.3 V TTL on TXD. CP2102, CH340 and
  FT232 boards all work this way. The method needs only TXD, so the
  adapter's RXD and RTS are not used.
- Three leads with test hooks, or soldered wires.
- A Windows, Linux or macOS PC with Python and `python -m pip install pyserial`.
- A multimeter for the continuity and voltage checks.

## 1. Get a recovery kit

Every run of the BSEED PM and non-PM role matrix workflows uploads a ready
kit for both roles, built and checked at that exact commit:

```text
gh run list --repo analienx/tuya-zigbee-switch --workflow "BSEED PM role matrix" --branch main --limit 1
gh run download <run id> --repo analienx/tuya-zigbee-switch -n bseed-pm-wired-kit-<commit sha>
```

To make a fresh one (artifacts expire), run
`gh workflow run "BSEED PM role matrix" --repo analienx/tuya-zigbee-switch --ref main`.
Use `BSEED non-PM role matrix` and `bseed-nonpm-wired-kit-<commit sha>` for
TS011F-BS sockets without metering.

A kit contains the tool and one bundle per role:

```text
telink_swire_recovery.py
router/image.bin   router/plan.json
client/image.bin   client/plan.json
```

`image.bin` is the finalized image inside `forward.ota`: padded, with its
size field and CRC set. That is what a device receives over the air and what
CI started in the TC32 model. `forward.bin` from the same build is the
linker output before finalization. Do not flash it.

You can also make a bundle from any single-image Zigbee OTA file:

```text
python telink_swire_recovery.py prepare --ota forward.ota --manifest manifest.json --role-matrix ROLE_MATRIX.json --out router --expect-build 1.2.5-bseedr22
python telink_swire_recovery.py prepare --ota GL-SD-301P_V20851203_20251230.ota --out gledopto --expect-ota-sha256 16595a38ab9783d3afc4eb58ab4ec32625249bd569c1fa6dbaf468bddc76dd72
```

`prepare` refuses an image with a bad CRC, a wrong size field, no start
marker, a second start marker at `0x8008` or `0x20008`, a size that reaches
the NVM, or an OTA header that differs from the image, such as a stock
conversion or FORCE wrapper. With a manifest and role matrix, it also
requires the same OTA hash, a clean source tree, one commit, and TC32 start
and activation evidence for that exact image. The second example produces
the same bytes as the stock image pinned by the
[Gledopto recovery kit](https://github.com/analienx/gledopto/pull/10), and CI
checks that.

`python telink_swire_recovery.py plan --bundle router` shows what a bundle
will write and what has been sent from it before.

## 2. Check the adapter on its own

With nothing connected to the device:

1. Set the adapter to TTL mode. Multi-protocol boards have a switch for
   this. Leave `232T`, `232R`, `A` and `B` unconnected.
2. `python telink_swire_recovery.py ports` lists the serial ports. Note
   the adapter's port, for example `COM5`.
3. Put a jumper between TXD and RXD, then run
   `python telink_swire_recovery.py selftest --port COM5`. It must print
   `PASS`. This checks the driver and the 460800 baud rate, nothing more.
4. Remove the jumper. Measure TXD against GND: it should idle at about
   3.3 V. If it reads 5 V or swings negative, stop: the adapter is not in
   3.3 V TTL mode.

## 3. Connect the device

| Adapter | Device | Notes |
| --- | --- | --- |
| `TXD` | `SWS` | the only signal |
| `GND` (`USBGND` on CP2102 boards) | `GND` | |
| `3V3` | `3V3` / `VCC` | or a separate 3.3 V supply sharing GND |
| `RXD`, `RTS`, `DTR`, `+5V` | — | leave unconnected |

Find the pads from the silkscreen and confirm each with a continuity test,
with everything unpowered:

- **BSEED sockets (Tuya ZTU module).** `SWS` is a castellated pad on the
  module edge, labelled `SWS` on the silkscreen side. The ZTU datasheet puts
  SWS on pin 4, GND on pin 13 and VCC on pin 14. `3V3` and `GND` are marked
  on the carrier board; use a pad or via that measures as the same net.
- **Gledopto GL-SD-301P.** A three-pad row next to the crystal, labelled
  `GND | 3.3V | SWS`.

Clips must hold by themselves and must not touch a neighbouring pad. The
chip does not answer, so a clip that slips during the write goes unnoticed.

## 4. Flash

```text
python telink_swire_recovery.py flash --bundle router --port COM5
```

The tool shows the image, the address range it will write and a short
checklist, then asks you to type `FLASH-` followed by the first eight hex
digits of the image SHA-256. Use `--confirm FLASH-xxxxxxxx` to skip the
prompt. A Router-size image takes about two and a half minutes. Do not touch
the leads until it prints that the chip restarted.

`flash` is `stage` followed by `activate`. They can be run separately:

- `stage` restarts and halts the chip, clears flash write protection,
  erases slot A sector by sector up to the end of the image, and writes the
  image with the first byte of its start marker left at `0xFF`. A second
  pass sends the same pages again without erasing, which repairs a page
  lost on the wire.
- `activate` writes the single byte `0x4B` at `0x08` without an erase,
  which turns the marker into `KNLT`, then restarts the chip.

Each step appends a line to `receipts.jsonl` in the bundle folder.
`SENT_UNVERIFIED` there means the PC sent the bytes. It does not mean the
chip received them.

Running `flash` again is safe. It erases slot A again, so the slot stays
unbootable until the new activation.

## 5. Check the result

Right after `flash`, while still on the adapter's power, the device should
show LED activity and respond to its button. The relay may not switch on
3.3 V from an adapter. If nothing happens, check the clips, the TXD level
and the 3.3 V rail under load, then run `flash` again.

Then disconnect the adapter, reassemble the device completely, and only
then connect it to mains. Treat the device as recovered only after fresh
evidence from Zigbee2MQTT:

- a new frame from the same IEEE address,
- the expected `swBuildId` in a Basic cluster read,
- relay control from Zigbee and from the button,
- for PM sockets, a read of the metering attributes. Do not wait for
  reports: after every boot, Router firmware holds metering reports for at
  least 6 minutes until it has heard from the coordinator.

## What the tool writes

| Range | Content | Tool |
| --- | --- | --- |
| `0x00000` to the end of the image, rounded up to 4 KiB | slot A | erased and written |
| rest of slot A, up to `0x33FFF` | unused | left as is |
| `0x34000`–`0x3FFFF` | Zigbee NVM: network key, bindings, energy | never written |
| `0x40000`–`0x73FFF` | slot B | never written |
| `0x76000`, `0x77000` | IEEE address, factory RF/ADC/voltage calibration | never written |
| `0x78000`–`0x7FFFF` | install code, reset counter, second NVM area | never written |

Those limits are hard-coded in the writer, not just in the plan. When slot B
also holds a start marker, both the SDK's `mcuBootAddrGet()` and the tool
assume slot A starts first. The next OTA update overwrites slot B and clears
slot A's marker in the normal way.

## Limits

- **No readback.** With only TXD connected, the chip never answers. A
  flash memory read needs a bidirectional SWire adapter, such as pvvx's
  [TlsrComSwireWriter](https://github.com/pvvx/TlsrComSwireWriter) circuit
  or [TLSRPGM](https://github.com/pvvx/TLSRPGM).
- **Lost erases are not repaired.** The second pass repairs lost page writes
  but not a lost sector erase. Run `flash` again if the device does not start.
- **Fixed timing.** The SWire timing (460800 baud, divider 55, 300 ms per
  sector, 10 ms per page) is pvvx's
  [USBCOMFlashTx v0.5](https://github.com/pvvx/pvvx.github.io/blob/master/ATC_MiThermometer/USBCOMFlashTx.html).
  The flash wait times are fixed delays, not status polling.
- **No slot B fallback.** If the recovered image does not start, the tool
  does not fall back to slot B. Run `flash` again with a known-good image.

## How it is tested

[`tests/test_telink_swire_recovery.py`](../../tests/test_telink_swire_recovery.py)
decodes every UART byte the writer sends back into SWire register writes. It
then applies them to a model of the TLSR8258 SPI controller and a 512 KiB
NOR flash that starts in the bricked state: an image enabled in slot A,
another disabled in slot B, and live NVM. The tests check that slot A ends
up byte-identical to the image and that nothing above it changes. They also
check that the marker stays off after `stage`, that a lost page is repaired
by the second pass but not by one pass, and that every flash command follows
a write enable while the CPU is halted. The frame encoding is checked
against vectors from pvvx's original JavaScript. None of this exercises a
real adapter or chip.

For BSEED devices, the repository's flashing rules still apply. Read
[the BSEED OTA skill](../../skills/bseed-zigbee-ota/SKILL.md) and the
device's incident record first; a wired flash is a hardware action that the
owner authorizes for the specific device.
