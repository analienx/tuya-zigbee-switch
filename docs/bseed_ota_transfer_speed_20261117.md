# BSEED OTA transfer speed: what the SDK actually does

Scope: the Telink `tl_zigbee_sdk` OTA client as built into every BSEED Router/Client image, and Zigbee2MQTT's own OTA server (`zigbee-herdsman`). Read-only source research; no live device was touched to produce it.

## The device's own request size is hardcoded at 48 bytes, not server-configurable

`zigbee/ota/ota.h` in the pinned SDK (V3.7.2.0) defines:

```c
#define OTA_IMAGE_MAX_DATA_SIZE 48
```

and the client always sets `req.maxDataSize = OTA_IMAGE_MAX_DATA_SIZE` on every `imageBlockReq` (`ota.c`, `ota_imageBlockReq`). This repo's own 2026-09-20 offline forensics independently recorded the same number from a live device ("48-byte device-requested maximum"), in `docs/bseed_ota_abort_forensics.md`.

Zigbee2MQTT's server takes `min(configured default_maximum_data_size, the device's own requested maxDataSize)` (confirmed in zigbee-herdsman's `src/controller/helpers/ota.ts`, `sendImageBlockResponse`). Since our device always requests 48:

- **A server cap below 48 (e.g. this project's historical 32-byte default) throttles below the device's own capability** — strictly more round trips than necessary for the same transfer, with no compensating benefit.
- **A server cap above 48 does nothing.** The device will never ask for more, however high `block_bytes`/`default_maximum_data_size` is set.

This repo's own 2026-09-24 sleepy-client incident (`SKILL.md`, the BedroomSocketCabinetRight write-up) already ran a 48-byte transfer to completion once paced, and explicitly recorded "**block size was not the variable**" — both 32- and 48-byte transfers stalled identically *unpaced*; only adding `response_delay_ms` pacing fixed it, not reducing block size. That incident is the direct precedent for treating 48 bytes as safe once pacing is in place, which is why this change raises `block_bytes` and leaves `response_delay_ms` alone.

**Decision (2026-11-17):** the canary tool's shared `PACING` default (`helper_scripts/bseed_canary.py`) now uses `block_bytes: 48`. `response_delay_ms` keeps its evidenced `1200` default — see below for why that one is not touched by default.

## Image Page Request is not available on this firmware — don't pursue it

Zigbee's OTA spec allows a client to request a whole page of blocks in one round trip instead of block-by-block, which is the standard way to cut per-block round-trip overhead. Checked both ends of this project's own pipeline:

- **The Telink client's state machine never sends one.** `ota.c`'s client path only ever calls `ota_sendImageBlockReq`/`ota_imageBlockReq`; there is no code path that builds or sends an `ota_imagePageReq`.
- **The Telink SDK's own server role explicitly refuses one**, returning `ZCL_STA_UNSUP_CLUSTER_COMMAND` from `ota_imagePageReqHandler` — irrelevant to us (we never act as OTA server), but confirms the SDK's page-request support is a stub, not a tested path.
- Current `zigbee-herdsman` *would* answer a page request if asked (`OtaSession` races `imageBlockRequest` against `imagePageRequest`), so this is purely a firmware-side gap, not a Z2M limitation.

Net: there is no lever here. Our devices will always transfer block-by-block regardless of server capability.

## Where the real pacing floor is, and why `response_delay_ms` isn't touched here

- **Flash writes are batched, not per-block.** `ota.c`'s `FLASH_WRITE_COUNT_GET(size)` commits roughly every 110 blocks. Per-block latency is not flash-write time.
- **Per-block timeout and retry ceiling are fixed in firmware:** `OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME = 5` seconds, `OTA_MAX_IMAGE_BLOCK_RETRIES = 10`. A genuinely stuck block costs up to 50 s before the client gives up and the transfer aborts — this is the floor `request_timeout_ms`/`image_block_request_timeout` needs to clear with margin, matching this skill's existing "size the request timeout for the paced transfer duration plus retry-storm margin" guidance.
- **`response_delay_ms` only has an effect above the device's own natural round-trip time**; zigbee-herdsman's `sendImageBlockResponse` throttles its own send rate from `#lastBlockResponseTime`, so setting it below the real round trip does nothing, and setting it above becomes the dominant, artificial bottleneck. Z2M's own documented default is 250 ms.
- **This repo has direct, specific incident evidence that removing pacing caused real stalls** (the 2026-09-24 write-up above) on a sleepy EndDevice with a weak/contended link. Lowering this value is a legitimate thing to try on a *specific* strong-link device (e.g. a Router-to-Router flash through an already-strong neighbor), but it is not something to change as a blanket default without a fresh canary confirming it — which is why this change leaves it at `1200` and raises only `block_bytes`.
- **The device honors a server-advertised ZCL `minBlockPeriod` as an *extra* enforced wait on top of `response_delay_ms`.** Confirmed `zigbee-herdsman` never sets this field, so there is no hidden stacking of two delays on the Z2M path today — worth re-checking if the server side ever changes.

## Parallel OTA overlaps dead time, not raw radio throughput

`zigbee-herdsman`'s `OtaSession` is a per-call object with no global lock, consistent with this repo's own opt-in `parallel_ota_max: 2` (PR #128). Two concurrent sessions still share one physical radio and one coordinator command queue, so running two devices "in parallel" does not double raw transfer throughput — it overlaps each device's *dead time* (retries, flash commits, post-OTA interview/audit) with the other device's active block transfer. That overlap is real and worth using; it is not a multiplier on top of the per-device numbers above.

## Sources

- `zigbee/ota/ota.h`, `zigbee/ota/ota.c`, Telink `tl_zigbee_sdk` V3.7.2.0 (the pinned SDK version this project builds against) — `OTA_IMAGE_MAX_DATA_SIZE`, `OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME`, `OTA_MAX_IMAGE_BLOCK_RETRIES`, `FLASH_WRITE_COUNT_GET`, the client's block-only request path, and the server role's `ota_imagePageReqHandler` stub.
- `Koenkk/zigbee-herdsman`, `src/controller/helpers/ota.ts` (current `master`) — `sendImageBlockResponse`'s throttle-from-last-response-time logic, the `imageBlockRequest`/`imagePageRequest` race, and the default `baseSize`/`requestTimeout` fallbacks.
- This repo's own `docs/bseed_ota_abort_forensics.md` (2026-09-20) and `skills/bseed-zigbee-ota/SKILL.md` (2026-09-24 BedroomSocketCabinetRight write-up).
