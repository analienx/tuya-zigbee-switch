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
interviewed and reports build `1.2.5-bseedr22` as a Router; a fresh interview
confirmed it again later with a live Router node descriptor.

The power reading stayed at a stale 2 W (a 2026-11-06 sample, taken before the
r21 OTA) for hours. Two separate causes, both outside the firmware:

- The Home Assistant converter predated r22 and listed builds only up to r21.
  It now recognises the whole BSEED PM build family instead of a per-release
  list, so new releases no longer need a converter update (PR #122).
- The r21 campaign had set `bseed_pm_telemetry_quarantine`, which makes
  Zigbee2MQTT discard power, current, voltage and energy until the campaign
  releases it. r21 never started, so nothing released it, and every release route
  expected the campaign's own build. The new `release-superseded` route (PR #125)
  releases it after a verified wired install, with fresh interview evidence, the
  SWire install receipt and a written reason, and leaves the campaign and
  network locks untouched.

After the release, a state refresh read fresh values straight from the device,
with a newly stamped power sample: 55 W, 0.30 A, 251.8 V, 0.54 kWh. The earlier
2 W was the stale sample. Zigbee2MQTT's update panel now shows r22's file
version.

## What this does and does not establish

Established: r22 starts on a real TLSR8258 board, rejoins on its stored
network record and reports fresh, plausible metering after the stale quarantine
was released; the r21 failure was in the r21 image, not in the OTA path.

Not yet established: an over-the-air install of r22, a loaded-to-zero metering
check, behaviour across power cycles and a soak. r22
remains an unsealed draft, not public. The owner cancelled the r21 campaign on
2026-11-13, which released the network-wide OTA lease (none is held now) and
left the PM quarantine in place until the release described above. The TS0726
`rv5`/`lv5` `-Os` images may now be planned for one-device canaries, one at a
time.
