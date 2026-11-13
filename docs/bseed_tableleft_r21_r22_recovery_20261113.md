# Table Left: r21 failure confirmed, r22 restored by wire (2026-11-13)

LivingRoomSocketTableLeft (BSEED TS011F-BS-PM, Router) stopped responding after
the over-the-air install of PM Router `1.2.5-bseedr21` on 2026-11-06. On
2026-11-13 it was read and rewritten over SWire with PM Router `1.2.5-bseedr22`
and is back on the network. This is the first start of an `-Os -fno-jump-tables`
build on real hardware.

## What the flash showed before the rewrite

Two matching full 512 KiB reads with the CPU paused (control `0x602` = `0x05`),
compared with the CI-built images:

| Area | Contents | Meaning |
| --- | --- | --- |
| Slot A `0x00000` | r19, **disabled** (only byte 8 zeroed), CRC valid | The SDK switched slots exactly as designed |
| Slot B `0x40000` | r21, **enabled**, byte-identical to the CI image, CRC valid | The whole OTA arrived intact |
| Settings `0x34000` | Network record present with the device's own IEEE | Rejoin without re-pairing is possible |

So the OTA transfer and the slot switch worked; r21 itself failed to start. This
fits the defect found earlier in the r21 binary and the TC32 model: the `-Os`
build emitted a malformed switch dispatch in the SDK's `flash_vdd_f_calib`. The
dispatch is wrong for any flash capacity byte, so it does not depend on this
board's calibration values. r22 builds with `-fno-jump-tables`, which removes that
code pattern. Earlier OTA installs of other builds on the same socket worked,
which is consistent with this.

## The rewrite

- Image: the CI-built r22 wire image (OTA payload from byte 62), 205,412 bytes,
  SHA-256 `87dd576a1307015d941efd3ceaba6b83a670a048b674308037718ccea9363139`, from
  the PM wired kit of the role-matrix run.
- `telink_swire_recovery.py flash`, TX-only, slot A (`0x00000`–`0x33000`), three
  passes, about 4 minutes. Settings, IEEE, calibration and slot B were not
  written.
- A later paused read (single read) showed slot A byte-identical to r22 with its
  marker set, and everything outside slot A unchanged except 24 bytes in one
  settings sector. Only r22 could have written them, so r22 ran past startup on
  the bench before it ever reached the network.

## On the network

Zigbee2MQTT shows the socket rejoined by itself (same IEEE, no pairing), was
interviewed and reports build `1.2.5-bseedr22` as a Router.

Two things are still open and are not firmware defects:

- Zigbee2MQTT kept showing the 2026-11-06 power sample (2 W with no load). The
  Home Assistant converter predates r22 and listed builds only up to r21, so r22
  readings got generic scaling and were never stamped. The converter now
  recognises the BSEED PM build family instead of a per-release list, and the
  post-update step refreshes cached state (PR #122).
- Zigbee2MQTT's update panel still showed r21's version number after a version
  check.

## What this does and does not establish

Established: r22 starts on a real TLSR8258 board and rejoins on its stored
network record; the r21 failure was in the r21 image, not in the OTA path.

Not yet established: an over-the-air install of r22, relay and metering
acceptance with fresh readings, behaviour across power cycles and a soak. r22
remains an unsealed draft, not public. The TS0726 `rv5`/`lv5` `-Os` images may
now be planned for one-device canaries, one at a time, after the r21 campaign
lock and network lease are formally closed.
