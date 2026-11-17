# BSEED OTA transfer speed: verified facts and open questions (2026-11-17)

Read-only source research; no device was touched. This record exists so the same questions are not re-investigated, and so two wrong conclusions from the first draft of this research are not repeated (see "Corrections" below).

**Decision (owner, 2026-11-17):** use 48-byte blocks wherever possible. `helper_scripts/bseed_ota_block_policy.py` enforces it for every campaign profile and in the non-PM recovery and two-slot gates:

- **Paced transfers** (`response_delay_ms` at least 1200), custom or stock firmware: 48 bytes. This is the canary default (`bseed_canary.py`) and the default whenever a profile leaves `block_bytes` unset.
- **Unpaced transfers:** 32 bytes.
- **Exact historical authorizations** (Bedroom, Balcony, Entrance): stay pinned at 32, as approved.

`response_delay_ms` stays 1200.

## Which OTA client is downloading matters first

An OTA image is downloaded by the firmware that is **currently running** on the socket:

- **Socket already on custom BSEED firmware** (any same-role refresh or role transition): the client is the pinned Telink `tl_zigbee_sdk` V3.7.2.0 OTA client compiled into this repo's images. The SDK facts below apply.
- **Stock Tuya socket, first conversion** (e.g. a `from_tuya` wrapper): the client is Tuya's own firmware. The SDK facts below are not proven for it; its retry and timeout behavior are unknown from source.

What this repo did record for a **stock** download (`docs/bseed_kitchenleft_ota_abort_triage_20260920.md`, KitchenSocketLeft stock `_TZ3000_b28wrpvx`, `cli6` wrapper, 2026-09-20):

- **The stock firmware requested 64-byte blocks.** Zigbee2MQTT never sends more than the device requests, so any cap up to 64 is within what it asked for.
- **It accepted 823 consecutive 50-byte blocks** (offsets 0 to 41100), then requested offset 41150 nine times and sent ABORT. The run was unpaced; a TS0726 dimmer OTA was already at 79% by 14:59, so an overlap with this 14:36–14:41 run is possible but unconfirmed. The cause was never established.
- **The 100-byte attempt aborted at 0%** while actually sending 64-byte blocks. That points at frame size (64 bytes plus headers), not at 48.
- **The 32-byte run that completed** only resumed the last ~74% from the retained partial image, also unpaced. It is not a controlled comparison.

Nothing here shows stock firmware failing at 48 bytes.

## Verified facts for the custom-firmware OTA client

All from `zigbee/ota/ota.h`, `zigbee/ota/ota.c` and `zigbee/zcl/ota_upgrading/zcl_ota_attr.c` at SDK tag V3.7.2.0:

- **Requested block size is fixed at 48 bytes.** `OTA_IMAGE_MAX_DATA_SIZE` is 48, and `ota_imageBlockReq` always sets `maxDataSize` to it. No override exists under `src/telink`. `docs/bseed_ota_abort_forensics.md` recorded the same value from a live device on 2026-09-20. Zigbee2MQTT serves `min(default_maximum_data_size, device request)` (`zigbee-herdsman` `src/controller/helpers/ota.ts`, `sendImageBlockResponse`). A cap above 48 therefore changes nothing for custom firmware.
- **Every block is written to flash as it arrives.** `ota_imageDataProcess` calls `flash_writeWithCheck(baseAddr + otaClientInfo.otaElementPos, copyLen, ...)` per block. `FLASH_WRITE_COUNT_GET` only throttles how often the **resume metadata** (`otaClientInfo`, NV item `NV_ITEM_OTA_CODE`) is saved, roughly every 110 blocks. Per-block flash-write time is part of every block's cost.
- **Per-block response wait and retries are fixed:** `OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME` 5 s, `OTA_MAX_IMAGE_BLOCK_RETRIES` 10.
- **No page-mode transfer.** The client only ever sends Image Block Requests; no path sends an Image Page Request. The SDK's server-side page handler returns `ZCL_STA_UNSUP_CLUSTER_COMMAND`. Zigbee2MQTT would answer page requests, but the device never makes one, so there is no lever here.
- **No hidden second delay from Zigbee2MQTT.** `zcl_attr_minBlockPeriod` defaults to 0 and is set only from a WAIT_FOR_DATA block response (`rsp.wait.blockReqDelay`). `zigbee-herdsman` does not send that status. Only `response_delay_ms` paces the transfer from the server side.
- **`response_delay_ms` is a minimum spacing between server responses**, measured from the previous response (`#lastBlockResponseTime`). It has no effect when the natural round trip is already longer.

## Why 48 bytes needs pacing

- **Every recorded ~48–50-byte failure was unpaced.** KitchenSocketRight `v8u5-rc1` (50-byte-ceiling ABORT) and stock `cli6` (50-byte stall) both ran on 2026-09-20. Response pacing was introduced after the 2026-09-24 incident.
- **The one paced 48-byte run completed.** BedroomSocketCabinetRight `cli4` → `cli5-rc1`, custom firmware, 1200 ms pacing. Unpaced, 32 and 48 bytes had stalled alike there.
- **Stock firmware asked for more than 48** and took 50-byte blocks for most of a transfer; its one stall was unpaced. The first paced stock conversion is the deciding data point. A stall there keeps the partial image and can resume at 32 without losing progress, as the `cli6` run did.

The first draft of this change set 48 without updating the gates that pinned 32 (`bseed_nonpm_recovery_gate.py`, the two-slot pilot in `bseed_network_campaign_lock.py`). Those gates now admit 32, or 48 under the same policy.

## Where the time actually goes

At the current 32 bytes and 1200 ms pacing, server pacing dominates:

| Image | Size | Blocks at 32 B | Time at 1200 ms | Blocks at 48 B | Time at 1200 ms |
|---|---:|---:|---:|---:|---:|
| PM Client `cli26` | 178 082 B | 5 566 | ~111 min | 3 711 | ~74 min |
| PM Router `r23` | 205 826 B | 6 433 | ~129 min | 4 289 | ~86 min |

Lower bound only; retries add to it. `response_delay_ms` is the larger lever. It is also the one setting this repo has direct incident evidence for: removing it caused real stalls (`MAC_BAD_STATE` collisions on the coordinator). Any reduction should be a **deliberate, single-device canary** on a socket with a strong link, not a default change. Measure blocks per second and stall count against a 1200 ms baseline before generalizing.

## Parallel OTA

`zigbee-herdsman` runs one `OtaSession` per device with no global lock. This is consistent with the opt-in two-slot pilot. Two sessions still share one radio and one coordinator queue, so the gain is overlapping each device's waits and post-OTA steps, not doubled transfer rate. The two-slot pilot itself requires ≥1800 ms pacing.

## Corrections to the first draft

- **Wrong:** "flash writes are batched about every 110 blocks, so flash-write time is not a bottleneck". Image data is written per block. Only resume metadata is batched.
- **Wrong scope:** the 48-byte, 5 s and 10-retry figures were presented as general. They apply only to sockets already running custom firmware. They say nothing about a stock socket's first conversion.
- **Incomplete:** the first draft set the canary default to 48 without updating the gates that pinned 32. They now admit 48 under the same rule.
- **Misread:** an intermediate version kept stock firmware at 32, describing the `cli6` run as "a 50-byte stall". The trace shows stock firmware requested 64 bytes and took 823 consecutive 50-byte blocks; its one stall was unpaced and unexplained.

## Sources

- Telink `tl_zigbee_sdk` V3.7.2.0: `zigbee/ota/ota.h`, `zigbee/ota/ota.c` (`ota_imageBlockReq`, `ota_imageDataProcess`, `ota_saveUpdateInfo2NV`, `ota_sendImageBlockReq`, `ota_imagePageReqHandler`), `zigbee/zcl/ota_upgrading/zcl_ota_attr.c`.
- `Koenkk/zigbee-herdsman` `master`: `src/controller/helpers/ota.ts`.
- This repo: `docs/bseed_ota_abort_forensics.md`, `docs/bseed_kitchenleft_ota_abort_triage_20260920.md`, `skills/bseed-zigbee-ota/SKILL.md` (cli6, KitchenSocketRight `v8u5-rc1` and BedroomSocketCabinetRight write-ups), `helper_scripts/bseed_nonpm_recovery_gate.py`, `helper_scripts/bseed_network_campaign_lock.py`.
