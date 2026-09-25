# Non-PM `cli5-rc2` canary qualification on BedroomSocketCabinetRight (2026-09-25)

Target: `BedroomSocketCabinetRight`, board `OUTLET_BSEED_TS011F`, non-PM
mains Rx-on EndDevice. Installed: `1.1.2-bseedcli4` string with
`fileVersion` 285356048 (rc1 bytes under a shared identity — the reason rc2
exists). Candidate: `1.1.2-bseedcli5-rc2` / `0x11023012` (type 65026),
registry hash recorded, WSL clean-tree build, offline packaging validated
(Telink magic, startup flag, embedded version, length, CRC).

Verdict: **NOT qualified — no flash, no retry.** The read-only OTA
eligibility check failed with the same intermittent signature seen on
2026-09-21: `Device didn't respond to OTA request` after the bounded 60 s
Zigbee2MQTT device timeout. The first link gate passed 3/3 fresh
non-retained relay reads (0.09–0.14 s, relay OFF, `follow_state`, build and
policy matched), so this is **not** general link loss. Per the OTA skill, a
second check is not an automated retry; the failed check leaves no
eligibility behind.

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

Next options (owner decision, all require explicit authorization):

1. Re-run read-only `qualify` timed near the device's spontaneous ~15 min
   OTA query cadence and see whether the notify→query sequence completes.
2. Diagnose the installed cut's notify handling (stale `cli4` string,
   shared-identity build) before spending more live attempts.
3. Designate a true bench spare instead of the production bedroom socket.
