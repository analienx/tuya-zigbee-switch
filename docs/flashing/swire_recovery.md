# Wired recovery over SWire

Use this when a Telink TLSR8258 device no longer starts or cannot take an OTA
update. It rewrites one selected image slot with a USB-UART adapter (A by default). The writer leaves the Zigbee network record, IEEE address, factory calibration
and saved energy outside its write range. Rejoining and retained-state compatibility
still need fresh device evidence.

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
python telink_swire_recovery.py prepare --device gledopto-gl-sd-301p --ota GL-SD-301P_V20851203_20251230.ota --out gledopto --expect-ota-sha256 16595a38ab9783d3afc4eb58ab4ec32625249bd569c1fa6dbaf468bddc76dd72
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
prompt. A Router-size image takes about four minutes. Do not touch the leads
until it prints that the chip restarted.

`flash` is `stage` followed by `activate`. They can be run separately:

- `stage` restarts and halts the chip, clears flash write protection and
  writes the image with the first byte of its start marker left at `0xFF`.
  It makes three passes: erase and write, erase and write again, then write
  only. Losing any one erase or page on the wire is repaired by a later
  pass. Reprogramming bytes that already arrived changes nothing.
- `activate` writes the single byte `0x4B` at the selected slot start plus `0x08` without an erase,
  which turns the marker into `KNLT`, then restarts the chip. It refuses
  unless the latest `stage` of the same image, on the same port, finished
  within the last 30 minutes. Slot, erase extent and baseline hash must also match.
  `--hold` omits the final restart so readback can precede the controlled bench start.

Each step appends a `STARTED` line to `receipts.jsonl` in the bundle folder
before it sends anything. When it ends, it adds `SENT_UNVERIFIED` if all the
bytes were sent, or `INTERRUPTED` otherwise. `SENT_UNVERIFIED` means the PC
sent the bytes. It does not mean the chip received them.

A new `flash` is another destructive write and requires an assessed recovery decision. It erases the selected slot again, so the slot stays
unbootable until the new activation.

## 5. Check the result

Right after `flash`, while still on the adapter's power, the device should
show LED activity and respond to its button. The relay may not switch on
3.3 V from an adapter. If nothing happens, check the clips, the TXD level
and the 3.3 V rail under load, then stop and assess the observed failure before another write.

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
| `0x00000`–`0x33FFF` | slot A | writable only when A is selected |
| `0x34000`–`0x3FFFF` | Zigbee NVM: network key, bindings, energy | never written |
| `0x40000`–`0x73FFF` | slot B | writable only when B is selected |
| `0x74000`–`0x7FFFF` | reserved, IEEE, factory calibration, install code, second NVM | never written |

`--slot A` is the default. `--slot B` requires `--baseline` from an unchanged,
matching full double read on the same serial port. The baseline must show a
valid, explicitly disabled A image and no legacy layout markers. If A is enabled,
it would take boot priority: the tool refuses to touch it as a side effect of
writing B. Resolve that unexpected state separately.

By default the writer erases to the image end rounded to 4 KiB, at least
`0x21000` bytes from the slot start. `--full-slot` erases all `0x34000` bytes
of the selected slot, including any old image tail. Bounds are enforced in
the writer itself. No command can erase or program the other slot or NVM.

In slot A the minimum erase clears stale layout markers at `0x8008` and
`0x20008`. When targeting B, those addresses belong to A and are kept;
any enabled legacy marker in the baseline blocks B recovery.

## Limits

- **TX-only has no readback.** With only TXD connected, the chip never answers. A
  flash memory read needs a bidirectional SWire adapter, such as pvvx's
  [TlsrComSwireWriter](https://github.com/pvvx/TlsrComSwireWriter) circuit
  or [TLSRPGM](https://github.com/pvvx/TLSRPGM).
- **One loss at a time.** The three passes repair one lost erase or page.
  They do not repair losses in two of the passes at the same address, such
  as a clip that keeps slipping. Stop and assess the physical setup if the device does not start.
- **Fixed timing.** The SWire encoding (460800 baud, divider 55) is pvvx's
  [USBCOMFlashTx v0.5](https://github.com/pvvx/pvvx.github.io/blob/master/ATC_MiThermometer/USBCOMFlashTx.html).
  The chip cannot report when it is busy, so the waits are fixed: 400 ms per
  sector erase, 10 ms per page and 50 ms per status write. Those are longer
  than pvvx's, to allow for slow flash chips.
- **No automatic fallback.** If the recovered image does not start, the tool
  does not automatically switch slots or erase another image. Use only the device incident record's separately selected fallback image.

## How it is tested

[`tests/test_telink_swire_recovery.py`](../../tests/test_telink_swire_recovery.py)
decodes every UART byte the writer sends back into SWire register writes. It
then applies them to a model of the TLSR8258 SPI controller and a 512 KiB
NOR flash that starts in the bricked state: an image enabled in slot A,
another disabled in slot B, and live NVM. The tests check that slot A ends
up byte-identical to the image, that stale markers at `0x8008` and `0x20008`
are erased, and that nothing above the erased range changes. They also check
that the marker stays off after `stage`, and that every flash command follows
a write enable while the CPU is halted. One lost frame (a page in pass 1 or 2,
or the sector-0 erase in pass 1 or 2) must be repaired. With fewer passes,
the test confirms that the same losses do break the image, so the extra
passes are not decoration. The frame encoding is checked against vectors
from pvvx's original JavaScript. None of this exercises a real adapter or chip.

For BSEED devices, the repository's flashing rules still apply. Read
[the BSEED OTA skill](../../skills/bseed-zigbee-ota/SKILL.md) and the
device's incident record first; a wired flash is a hardware action that the
owner authorizes for the specific device.


## Optional reading and verification

Reading is optional. TX-only `stage`, `activate` and `flash` keep working without
it. A device-specific incident procedure can still require reading; in particular,
Table Left's r21/r22 handover requires identifying the failed slot and rewriting
that same slot. Use `inspect`, explicit `--slot A` or `--slot B`, `--full-slot`,
and the original matched `--baseline` for that procedure. Unknown flash state
is a stop condition, not permission to choose A by default.

For reading, use the **bidirectional circuit** documented by
[pvvx TlsrComSwireWriter](https://github.com/pvvx/TlsrComSwireWriter/blob/042613b1b47a1598856b0ff3e8a133f2918798a5/README.md).
An ordinary UART RX pin or a passed loopback is not proof of SWire read support.
The upstream reader warns about RX LEDs and FTDI adapters. Establish the actual
adapter/circuit first; do not attach RX using the TX-only wiring table above.

```text
python telink_swire_recovery.py read --port COM5 --out C:/private/recovery/before.bin --json
```

The command downloads the pinned `TLSR825xComFlasher.py` next to the dump, checks
its full SHA-256, and invokes only its `rf` command twice at 921600 baud. An offline
copy can be provided with `--reader PATH`; it must match the same hash. It saves
`before.bin`, `before.bin.second.bin` and `before.bin.json`. Outputs must be outside
Git checkouts and must not already exist. Full dumps contain network keys: keep
them private and never upload them as CI artifacts. A bounded diagnostic read is
available with `--address 0 --size 0x100`.

Reading changes volatile debug/SPI registers; it is not passive Zigbee observation.
By default no reset/halt is requested. `--halt` invokes upstream activation, which
resets and halts the CPU **and toggles RTS/DTR**. Leave these pins disconnected unless
their reset circuit is independently qualified. No restart is requested by `read`.
Do not run the application between two preservation reads: live NVM writes can
legitimately change the dump. Timeout, short read or disagreement fails the command
and keeps the evidence. There is no automatic write or TX-only fallback.

When a qualified setup can hold the CPU stopped, verify a staged slot before
activation using a second full read and the original backup:

```text
python telink_swire_recovery.py verify --bundle router --dump C:/private/recovery/after.bin --staged --baseline C:/private/recovery/before.bin --json
```

This compares every image byte (with the inactive marker), the erased tail and
all bytes outside the erase range, including the other slot and factory/NVM data.
Omit `--staged` to compare an activated image; omit `--baseline` for image-only
verification. `FLASH_MATCHED` is a byte comparison, never proof of boot. The read
receipt independently establishes whether the two source reads matched.

## Device profiles and repeat use

`prepare --device bseed-ts011f` (the default) supports PM/non-PM Router and Client
artifacts with their own manifest, role, build and exact-SHA role-matrix evidence.
Use finalized OTA payloads, never pre-packaging `forward.bin`.

`prepare --device gledopto-gl-sd-301p --out gledopto` downloads the hash-pinned
vendor stock OTA and produces its finalized image. Local stock OTA or finalized
stock BIN inputs are also accepted. The dedicated SWire workflow publishes a
`gledopto-stock-wired-kit-<sha>` artifact containing image, plan, tool and guide.
Quarantined Gledopto custom firmware is rejected by this profile. Supporting the
image format does not lift a firmware deployment quarantine.

`wiring --device NAME` supplies device-specific preparation notes. `--json` gives
structured output. `flash`, `stage` and `activate` accept `--dry-run FILE` to save
the UART stream and operation trace without opening a port. `--power-cycle` gives
an interactive low-voltage power-up window; it never controls mains power.


## Identify and replace the failed image in place

OTA alternates between two slots. A device name, cached Zigbee build or successful
OTA transfer does not record which physical slot was activated. Read the full
flash twice before writing, with the qualified bidirectional setup. Then supply
known firmware files from their original hosted artifacts:

```text
python telink_swire_recovery.py inspect --dump C:/private/recovery/before.bin --reference r19=C:/private/images/r19.ota --reference r21=C:/private/images/r21.ota --replace r21 --expect-other r19 --json
```

`inspect` requires both matching dump files and their successful read receipt.
For each slot it reports the real marker state, validates the declared size and
CRC, and compares the whole image with each reference. OTA disables an old image
by changing just byte 8 from `0x4B` to `0x00`; inspection restores that byte **in
memory only** to compare its CRC/hash with the original firmware. A staged `0xFF`
marker is reported separately. Reference inputs can be native OTA containers or
finalized raw images; pre-packaging linker binaries are refused.

`--replace r21 --expect-other r19` returns `replaceSlot: A` or `B` only when one
slot contains the exact enabled r21 image and the other the exact disabled r19
image. Two enabled markers, a partial/corrupt image, a different image, unknown
marker or legacy layout marker stops this decision. The report identifies stored
bytes and boot eligibility; it never proves the firmware executed.

Use the returned slot for **every** subsequent command. The example below is
only for an inspection result of **B**; substitute A throughout if A was found:

```text
python telink_swire_recovery.py plan --bundle router --slot B --full-slot
python telink_swire_recovery.py stage --bundle router --port COM5 --slot B --full-slot --baseline C:/private/recovery/before.bin
python telink_swire_recovery.py read --port COM5 --out C:/private/recovery/staged.bin
python telink_swire_recovery.py verify --bundle router --dump C:/private/recovery/staged.bin --staged --slot B --full-slot --baseline C:/private/recovery/before.bin
python telink_swire_recovery.py activate --bundle router --port COM5 --slot B --full-slot --baseline C:/private/recovery/before.bin --hold
python telink_swire_recovery.py read --port COM5 --out C:/private/recovery/activated.bin
python telink_swire_recovery.py verify --bundle router --dump C:/private/recovery/activated.bin --slot B --full-slot --baseline C:/private/recovery/before.bin
```

Keep the CPU halted between stage, reading and verification; do not power-cycle
or ask the reader to reset it during those steps. After verified activation with
`--hold`, perform the controlled low-voltage bench start from the incident guide.
The activation receipt is tied to slot, erase extent, original baseline hash,
image hash and serial port, and expires after 30 minutes. A different layout uses
a different confirmation token, such as `STAGE-B-FULL-<hash prefix>`.

The full-slot verifier checks the exact image, `0xFF` through the slot end,
and **every byte outside the selected slot** against the baseline, including the
entire other slot. This implements in-place recovery in either direction. Existing
TX-only workflows can still use the default A without optional readback, except
where a device-specific recovery procedure requires it.
