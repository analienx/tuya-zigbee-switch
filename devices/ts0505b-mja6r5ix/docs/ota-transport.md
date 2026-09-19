# TS0505B OTA transport research

## Safety property

`zigbee2mqtt/extensions/ts0505b_ota_acceptance_probe.mjs` is a pre-byte transport probe.

It:

1. reads a local runtime-only JSON configuration;
2. triggers a standards-compliant Image Notify;
3. answers the stock Query Next Image request with one test tuple;
4. records whether the client requests image block/page data;
5. responds to the first data request with Zigbee OTA `ABORT (0x95)`; and
6. moves to the next test case.

It never returns an Image Block Response with `SUCCESS`, so it never supplies firmware payload bytes to the client.

The committed example configuration contains no target address. Real target identifiers belong only in the local runtime sidecar and must not be committed.

## Known stock tuple

`manufacturerCode=0x100B`, `imageType=0x020C`, `fileVersion=0x10003607`, `fieldControl=0`.

The first custom D0 offer used `fileVersion=0x10003608` and `imageSize=304602`. Two notification paths both stopped before image block 0.
Post-attempt client attributes reported `fileOffset=0xFFFFFFFF`, `downloadedFileVersion=0xFFFFFFFF`, and idle upgrade status.

## Size blocker status

Tuya's MG21 resource table documents maximum firmware sizes of:

- 376 KiB for EFR32MG21A020F768IM32;
- 528 KiB for EFR32MG21A020F1024IM32.

The D0 image is 304,602 bytes (~297.5 KiB), below the smaller documented ceiling. This rules out the published full-firmware ceiling as a sufficient explanation for the pre-block refusal.

## First probe matrix

Keep manufacturer and image type fixed. Run exactly one case per unique local `run_id` and vary one dimension at a time:

- size sweep at `0x10003608`: 64 KiB, 128 KiB, 192 KiB, 256 KiB, 304602 bytes;
- if size is not causal, version sweep at a conservative fixed size: `+1`, `+0x100`, `+0x10000`.

An `accepted_prebyte=true` result means only that stock firmware requested block 0. The probe aborts immediately and is **not** evidence that the bootloader would accept or activate a candidate.


## Replay protection

Every runtime sidecar must use a unique `run_id`, set `armed=true` locally, and configure global automatic OTA checks disabled. The extension reserves a persistent sentinel before the first Image Notify. If Zigbee2MQTT restarts unexpectedly, the same run ID is blocked instead of replaying an offer.

A run with `query_seen=false` is inconclusive, not evidence of a rejected image. Never publish firmware data or deploy a candidate from a metadata-only result.

## Same-path 64-KiB versus full-size comparison (2026-09-19)

Both one-case runs used the same source-hashed metadata-only probe, one stock TS0505B, manufacturer `0x100B`, image type `0x020C`, and offered version `0x10003608`. Both received the stock Query Next Image request; no firmware payload bytes were sent.

| Advertised bytes | Block 0 requested? | Observation/result |
| ---: | :---: | --- |
| 65,536 | Yes | Aborted immediately with OTA `0x95` |
| 304,602 | No | No block request within 45 seconds |

The same-path discrepancy supports a size-sensitive stock acceptance or storage-policy gate **under the tested conditions**, but does not establish a monotonic size threshold, an exact slot capacity, or why the client declined the larger offer. The documented 376-KiB MG21 firmware build ceiling is not evidence of the installed device's OTA staging capacity. Next, use single-case, zero-payload probes around 128/192/256 KiB and compare identical offer paths before attempting any firmware transfer. Bootloader policy, board outputs, stock rollback and candidate installation remain unverified.
