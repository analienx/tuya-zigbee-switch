# ZG-204ZL bench launcher

`helper_scripts/flash_zg204zl.py COM3` composes the existing pinned pvvx reader
and original pvvx writer for the owner-selected ZG-204ZL conversion procedure.
Use only the same physically qualified ZG-204ZL board/interface as the first
sensor. It cannot identify the connected PCB electrically. The regulated 2.1 V
rail is not a 3.3 V input. Follow the device-specific voltage/pad record.

## ZG-204ZL-3.0 programming pins

Owner-confirmed orientation, recorded 2026-11-19: look at the **component side**,
with the **five programming holes along the top** and the **small push button
toward the upper-right corner**. From **left to right**:

| Pin | Signal | MCU pin / net |
| --- | --- | --- |
| 5 (leftmost) | RX | PB7 |
| 4 | TX | PB1 |
| 3 (middle) | SWS | PA7 |
| 2 | GND | Ground |
| 1 (rightmost) | +VBAT | Battery input |

```text
Component side, programming holes at top:
   [5 RX/PB7] [4 TX/PB1] [3 SWS/PA7] [2 GND] [1 +VBAT]
                                             button ↗
```

The reverse-side view is mirrored. SWire uses pin 3 (SWS), pin 2 (GND), and
the board's qualified supply connection. Pins 4 and 5 are the separate UART;
they are not substitutes for SWS. +VBAT is the battery input, not the regulated
2.1 V output; this pin map does not establish a safe externally applied voltage.

## Read, write and verify

The owner explicitly selected ONE full pre-write backup and ONE full post-write
read on 2026-11-19. This is a dedicated sensor procedure; it does not change the
BSEED verified-session double-read policy. A single backup does not prove read
repeatability. No original sensor dump, IEEE or network configuration is copied
to a different sensor.

The launcher downloads and checks the immutable ZigbeeTLc v0.1.4.0 native image
and the pinned pvvx tool, waits up to five minutes for USB/target entry, then:

1. Halts the CPU and reads the entire 512 KiB once into a new private run folder.
2. Calls only pvvx `wf 0x0` with the pinned 129,140-byte native image. It writes
   firmware sectors 0x00000..0x1FFFF, with no reset/run flag or erase-all.
3. Reads the entire flash once, without resetting or restarting.
4. Compares the exact image, erased tail, and all bytes at 0x20000..0x7FFFF
   against that sensor's fresh backup. Any mismatch stops the operation.

Keep the same target/power/contacts attached throughout. It does not select a
slot using the BSEED dual-slot strategy and does not clear any other flash range.
It leaves the CPU stopped after success. Perform a controlled sensor power cycle
to boot, then independently check pairing/interview, motion, lux and battery.
The script never enables joining, changes HA, or claims boot from a byte match.

Progress and evidence: `~/.telink-swire/zg204zl-runs/RUN/summary.json`, with
`before.bin`, `after.bin`, `write.log`, reader logs and `progress.log` alongside.
All are private; do not commit them. A shared SWire port lock spans the complete
operation and existing held recovery sessions block entry. Failed/uncertain
writes leave a durable stop at `~/.telink-swire/zg204zl/PORTKEY.json` and cannot
be automatically retried by changing output folders. Inspect actual flash and
assess recovery before retiring that stop. Do not delete state to bypass it.

If a write finishes but the verification read fails, keep the same sensor powered
and connected, then run `flash_zg204zl.py COM3 --verify-only`. This reads once and
compares against the saved original backup and pinned image. It never programs,
erases, resets or re-halts the CPU. It checks that the CPU remains stopped and
reuses the backup's verified SWire divider instead of accepting a new marginal
auto-speed result. Each attempt has its own file; failed evidence is retained.
Only a successful complete comparison changes the durable stop to VERIFIED.

Hosted model tests cover read/write order, one read per phase, image/tail/data
mismatches, failed backups, failed writes and durable stops, and other recovery
ownership. Hosted CI also downloads the original pinned upstream image. Those
checks establish source behavior and provenance, not a second physical flash.
