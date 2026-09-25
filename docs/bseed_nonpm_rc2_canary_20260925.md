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
