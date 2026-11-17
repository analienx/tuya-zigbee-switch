# BSEED OTA transfer speed: verified facts and open questions (2026-11-17)

Read-only source research; no device was touched. This record exists so the same questions are not re-investigated, and so two wrong conclusions from the first draft of this research are not repeated (see "Corrections" below). **It changes no default.** `block_bytes` stays 32 and `response_delay_ms` stays 1200 in `bseed_canary.py`.

## Which OTA client is downloading matters first

An OTA image is downloaded by the firmware that is **currently running** on the socket:

- **Socket already on custom BSEED firmware** (any same-role refresh or role transition): the client is the pinned Telink `tl_zigbee_sdk` V3.7.2.0 OTA client compiled into this repo's images. The SDK facts below apply.
- **Stock Tuya socket, first conversion** (e.g. a `from_tuya` wrapper): the client is Tuya's own firmware. None of the SDK facts below are proven for it. Its request size, retry and timeout behavior are unknown from source.

This repo's own evidence for **stock** downloads (`SKILL.md`, stock-facing PM Client `cli6`): a 100-byte cap aborted at 0%, a 50-byte cap stalled at offset 41150, and a 32-byte cap resumed and completed. For first conversions, 32 bytes is the only evidenced value.

## Verified facts for the custom-firmware OTA client

All from `zigbee/ota/ota.h`, `zigbee/ota/ota.c` and `zigbee/zcl/ota_upgrading/zcl_ota_attr.c` at SDK tag V3.7.2.0:

- **Requested block size is fixed at 48 bytes.** `OTA_IMAGE_MAX_DATA_SIZE` is 48, and `ota_imageBlockReq` always sets `maxDataSize` to it. No override exists under `src/telink`. `docs/bseed_ota_abort_forensics.md` recorded the same value from a live device on 2026-09-20. Zigbee2MQTT serves `min(default_maximum_data_size, device request)` (`zigbee-herdsman` `src/controller/helpers/ota.ts`, `sendImageBlockResponse`). A cap above 48 therefore changes nothing for custom firmware.
- **Every block is written to flash as it arrives.** `ota_imageDataProcess` calls `flash_writeWithCheck(baseAddr + otaClientInfo.otaElementPos, copyLen, ...)` per block. `FLASH_WRITE_COUNT_GET` only throttles how often the **resume metadata** (`otaClientInfo`, NV item `NV_ITEM_OTA_CODE`) is saved, roughly every 110 blocks. Per-block flash-write time is part of every block's cost.
- **Per-block response wait and retries are fixed:** `OTA_MAX_IMAGE_BLOCK_RSP_WAIT_TIME` 5 s, `OTA_MAX_IMAGE_BLOCK_RETRIES` 10.
- **No page-mode transfer.** The client only ever sends Image Block Requests; no path sends an Image Page Request. The SDK's server-side page handler returns `ZCL_STA_UNSUP_CLUSTER_COMMAND`. Zigbee2MQTT would answer page requests, but the device never makes one, so there is no lever here.
- **No hidden second delay from Zigbee2MQTT.** `zcl_attr_minBlockPeriod` defaults to 0 and is set only from a WAIT_FOR_DATA block response (`rsp.wait.blockReqDelay`). `zigbee-herdsman` does not send that status. Only `response_delay_ms` paces the transfer from the server side.
- **`response_delay_ms` is a minimum spacing between server responses**, measured from the previous response (`#lastBlockResponseTime`). It has no effect when the natural round trip is already longer.

## Why block size is not changed

The first draft of this research raised the canary default to 48. That change was withdrawn because:

1. **It breaks existing gates.** `bseed_nonpm_recovery_gate.py` refuses any non-PM flash unless `block_bytes == 32`, and the two-slot pilot (`bseed_network_campaign_lock.py`, `parallel_ota_max: 2`) refuses anything but 32. A 48-byte canary default would fail those runs before transfer.
2. **PM evidence points the other way.** The "block size was not the variable" finding came from one non-PM sleepy Client (BedroomSocketCabinetRight). For the PM board, `SKILL.md` records two cases where a ~48–50-byte transfer failed and a 32-byte retry completed (KitchenSocketRight `v8u5-rc1` after a 50-byte-ceiling ABORT; stock `cli6` above). Neither abort cause is proven, but neither supports 48 as a default.

## Where the time actually goes

At the current 32 bytes and 1200 ms pacing, server pacing dominates:

| Image | Size | Blocks at 32 B | Time at 1200 ms | Blocks at 48 B | Time at 1200 ms |
|---|---:|---:|---:|---:|---:|
| PM Client `cli26` | 178 082 B | 5 566 | ~111 min | 3 711 | ~74 min |
| PM Router `r23` | 205 826 B | 6 433 | ~129 min | 4 289 | ~86 min |

Lower bound only; retries add to it. `response_delay_ms` is the larger lever. It is also the one setting this repo has direct incident evidence for: removing it caused real stalls (`MAC_BAD_STATE` collisions on the coordinator). Any reduction should be a **deliberate, single-device canary** on a socket already running custom firmware with a strong link, not a default change. Measure blocks per second and stall count against a 1200 ms baseline before generalizing.

## Parallel OTA

`zigbee-herdsman` runs one `OtaSession` per device with no global lock. This is consistent with the opt-in two-slot pilot. Two sessions still share one radio and one coordinator queue, so the gain is overlapping each device's waits and post-OTA steps, not doubled transfer rate. The two-slot pilot itself requires ≥1800 ms pacing.

## Corrections to the first draft

- **Wrong:** "flash writes are batched about every 110 blocks, so flash-write time is not a bottleneck". Image data is written per block. Only resume metadata is batched.
- **Wrong scope:** the 48-byte, 5 s and 10-retry figures were presented as general. They apply only to sockets already running custom firmware. They say nothing about a stock socket's first conversion.
- **Unsupported:** raising the canary default to 48 bytes. It conflicts with pinned 32-byte gates and with the PM evidence above.

## Sources

- Telink `tl_zigbee_sdk` V3.7.2.0: `zigbee/ota/ota.h`, `zigbee/ota/ota.c` (`ota_imageBlockReq`, `ota_imageDataProcess`, `ota_saveUpdateInfo2NV`, `ota_sendImageBlockReq`, `ota_imagePageReqHandler`), `zigbee/zcl/ota_upgrading/zcl_ota_attr.c`.
- `Koenkk/zigbee-herdsman` `master`: `src/controller/helpers/ota.ts`.
- This repo: `docs/bseed_ota_abort_forensics.md`, `skills/bseed-zigbee-ota/SKILL.md` (cli6, KitchenSocketRight `v8u5-rc1` and BedroomSocketCabinetRight write-ups), `helper_scripts/bseed_nonpm_recovery_gate.py`, `helper_scripts/bseed_network_campaign_lock.py`.
