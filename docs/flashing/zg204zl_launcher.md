# ZG-204ZL bench launcher

`helper_scripts/flash_zg204zl.py COM3` composes the existing pinned pvvx reader
and original pvvx writer for the owner-selected ZG-204ZL conversion procedure.
Use only the same physically qualified ZG-204ZL board/interface as the first
sensor. It cannot identify the connected PCB electrically. The regulated 2.1 V
rail is not a 3.3 V input. Follow the device-specific voltage/pad record.

## Flash the v0148 coin-cell candidate

On the bench machine with the candidate already staged, run from Command Prompt
or PowerShell:

```bat
C:\Workspace\tools\telink-swire\flash-zg204zl.cmd COM3 --version v0148
```

v0148 uses nominal +2.61 dBm VANT, one discovery scan attempt, and the channel
restored from NVM after a failed startup rejoin. v0146 and v0147 used three
scan attempts and could lose knowledge of that channel after cold boot.
The v0148 source changes reduce recovery receive load. The suspected
voltage-sag and deep-sleep loop still needs measurement on the actual sensor.

From a checkout of the launcher branch, download and extract the exact
[GitHub Actions build](https://github.com/analienx/tuya-zigbee-switch/actions/runs/44793479388)
from firmware source `efdb84019f6af53547fc32d764a0af2843a3462b` in
[PR #152](https://github.com/analienx/tuya-zigbee-switch/pull/152). Pass the
matching native image path explicitly:

```bat
helper_scripts\flash-zg204zl.cmd COM3 --version v0148 --image "C:\path\to\ZG204ZL_v0148.bin"
```

Replace `COM3` with the adapter's actual port. The launcher checks each native
image's size and exact SHA-256 before serial access. Without `--image`, custom
versions use their staged file under `~/.telink-swire/tools/zg204zl/`. With no
version option the launcher selects the original v0.1.4.0 conversion image.

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

If the complete backup was saved and the operation stopped before writing,
keep the same sensor powered and attached, then resume with:

```bat
C:\Workspace\tools\telink-swire\flash-zg204zl.cmd COM3 --write-saved
```

This uses the last saved operation on that port. It checks the backup and
firmware hashes, confirms the CPU is still stopped and matches eight identity
bytes from the attached target. It then writes and performs one full verifying
read. The original full backup is reused. A changed or restarted target requires
inspection. A previously attempted write uses the existing --verify-only path.

The owner explicitly selected ONE full pre-write backup and ONE full post-write
read on 2026-11-19. This is a dedicated sensor procedure; it does not change the
BSEED verified-session double-read policy. A single backup does not prove read
repeatability. No original sensor dump, IEEE or network configuration is copied
to a different sensor.

The launcher checks the selected native image and the pinned pvvx tool, waits
up to five minutes for USB/target entry, then:

1. Halts the CPU and reads the entire 512 KiB once into a new private run folder.
2. For v0145 and later, checks for a supported ZG-204ZL image at zero and no active
   alternate boot image at 0x20000 or 0x40000. It then calls only pvvx `wf 0x0`
   with the selected native image. It writes
   firmware sectors 0x00000..0x1FFFF, with no reset/run flag or erase-all.
3. Reads the entire flash once, without resetting or restarting.
4. Compares the exact image, erased tail, and all bytes at 0x20000..0x7FFFF
   against that sensor's fresh backup. Any mismatch stops the operation.

Stock identity 0x1286/0x0203, version 0x20013001, declares 131300 bytes.
The launcher recognizes this revision even though it extends 228 bytes above
0x20000. Conversion preserves that old tail at 0x20000..0x200E3, along with
all other bytes above the new application's erase area. Unsupported headers
report their identity, version and declared size for inspection.

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
and connected, then rerun the launcher with `COM3 --verify-only`. It selects the
image pin from the saved operation, then reads once and compares against the
saved backup and image. It never programs,
erases, resets or re-halts the CPU. It checks that the CPU remains stopped and
reuses the backup's verified SWire divider instead of accepting a new marginal
auto-speed result. Each attempt has its own file; failed evidence is retained.
Only a successful complete comparison changes the durable stop to VERIFIED.

Hosted model tests cover read/write order, one read per phase, image/tail/data
mismatches, failed backups, failed writes and durable stops, and other recovery
ownership. Hosted CI also downloads the original pinned upstream image. Those
checks establish source behavior and provenance, not a second physical flash.
