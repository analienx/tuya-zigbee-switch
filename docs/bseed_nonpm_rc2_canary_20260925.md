# Non-PM `cli5-rc2` canary qualification on BedroomSocketCabinetRight (2026-09-25)

Target: `BedroomSocketCabinetRight`, board `OUTLET_BSEED_TS011F`, non-PM
mains Rx-on EndDevice. Installed: `1.1.2-bseedcli4` string with
`fileVersion` 285356048 (rc1 bytes under a shared identity — the reason rc2
exists). Candidate: `1.1.2-bseedcli5-rc2` / `0x11023012` (type 65026),
registry hash recorded, WSL clean-tree build, offline packaging validated
(Telink magic, startup flag, embedded version, length, CRC).

Verdict after the first attempt: **NOT qualified — no flash, no retry.**
The read-only OTA eligibility check failed with the same intermittent
signature seen on 2026-09-21: `Device didn't respond to OTA request` after
the bounded 60 s Zigbee2MQTT device timeout. The first link gate passed 3/3
fresh non-retained relay reads (0.09–0.14 s, relay OFF, `follow_state`,
build and policy matched), so this is **not** general link loss.

Log forensics (read-only Z2M logs, check transaction preserved in the
private scratch workdir):

- The device issues spontaneous periodic OTA queries roughly every 15 min
  (12:04:56, 12:19:56); the server answered both with status 152
  (`NO_IMAGE_AVAILABLE`, correct — no newer image in the configured index).
- At 12:21:04 the targeted check sent `imageNotify` (`payloadType` 0,
  `queryJitter` 100) after a 0.1 s relay read on the same device (LQI 153).
  No `queryNextImageRequest` followed within 60 s; no MAC/transmit fault was
  logged and the rest of the mesh stayed healthy.
- So the downlink works (fast reads) and the spontaneous query timer works,
  but the server-initiated notify→query sequence stalled this time. On
  2026-09-21 the same check later passed, confirming intermittency rather
  than a hard OTA-service failure.

The private one-target index and image server were HA-verified (image SHA
matches the registry identity) and the server was stopped after the halted
qualification. No relay, coordinator, channel, permit-join, power or pairing
state was changed at any point.

## Evening — FLASHED, upgradeEnd 19:42:06, rc2 verified running

With owner confirmation (exact IEEE, no load, risk accepted) and the waiver
extended to the exact rc2 transfer, timed flashes ran against the device's
900 s spontaneous query cadence (later 5-min retry cadence mid-campaign).
Two `update_error` attempts (query-stage timeouts, zero bytes each,
reconciled per precedent) and one 40-min runner watch preceded the result.
The transfer then flowed in paced 32-byte sessions with resume from kept
offsets (0 → 5792 → 51008 → 157728/158738): `upgradeEndResponse` sent
19:42:06, Z2M logged "Update successful" and "OTA update finished", the
device rebooted, re-announced on the same NWK, and re-interviewed
(descriptors, endpoints, modelId, manufacturerName, live Tuya datapoint
telemetry).

Postflash proof (all from the wire, not Z2M cache):

- Running `fileVersion` 285356050 in the device's own spontaneous
  `queryNextImageRequest` (19:57:36).
- Live `readRsp`: `dateCode` "20260925", `swBuildId` "1.1.2-bseedcli5-rc2"
  (19:42:35, seconds after the upgrade reboot). Pre-upgrade the same read
  returned "20260921" / "1.1.2-bseedcli5-rc1".
- Relay OFF throughout; post-upgrade link gate 3/3 fast reads; availability
  online; no load was ever toggled.

Cache lesson: Z2M `bridge/devices` kept showing the stale `cli4` string
through two interviews. Postflash acceptance must read raw `readRsp`
(or the device's own OTA query), never the bridge cache. The private image
server was stopped after completion.

## Second attempt 13:04 — QUALIFIED, no flash

The installed firmware queries OTA spontaneously every 900 s on the second
(12:04:56, 12:19:56, 12:34:56 observed). A deliberate fresh check was fired
at 13:04:00 so Z2M's 60 s window contained the 13:04:56 spontaneous query:
`update_available: true`, `latest_version` 285356050 from the private
one-entry index (check transaction preserved in the private scratch
workdir). A new link gate started after the successful check passed 3/3
(completed 13:06:13). Full `QUALIFICATION_PASSED_NO_FLASH` series complete;
the image server stayed up for the series and was stopped afterwards.

Flash did **not** proceed: it needs separate exact-IEEE confirmation, an
explicit load-unplugged attestation, and an applicable rc2 recovery path —
the signed-off non-invasive waiver covers only the `cli4`→`cli5-rc1`
transfer and does not transfer to rc2. Link evidence expires ~13:16, OTA
eligibility ~13:35; any flash needs a fresh series anyway.

## 14:51 flash attempt — update_error, reconciled (owner confirmed IEEE, no load, risk accepted)

The waiver was extended to the exact rc2 transfer (repo commit) and CI is
green on it. A timed flash fired at ~15:04:11 for the 15:04:56 spontaneous
query failed at the query stage: `Device didn't respond to OTA request`
after ~60 s (Z2M-side initial wait; the 30-min block timeout only governs
after transfer starts). Zero bytes transferred; the device stayed idle and
fast-reading. The `update_error` lock was archived verbatim and the active
slot cleared per the kitchenleft reconcile precedent — no new workdir, no
evasion. Lesson: the update must be fired ~50 s before a spontaneous query
(the device queries every 900 s sharp); the qualify chain (check catches one
query, post-gate, fire before the next) fits inside the evidence lifetimes
(check 30 min, gate 10 min).

## 15:14–15:19 stood down — third-party join operations on the mesh

A fresh series for the 15:19:56 query was refused twice by the join guards
(`Permit join open` in link-gate at 15:14:14, in check at 15:19:02 —
correct fail-closed behavior). Logs show another actor (`aldjs`
transactions) opening joins repeatedly, including router-scoped joins via
HallBreakerMain (15:00:42 network-wide 254 s, 15:12:51 via HallBreakerMain,
close 15:14:21, open again 15:15:20). No OTA is attempted while foreign join
activity is in progress. Resume only after the owner confirms the pairing
activity is finished and join stays closed.
