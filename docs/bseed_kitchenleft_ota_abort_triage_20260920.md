# KitchenSocketLeft stock-to-PM-Client OTA ABORT: evidence and next gate

Date: 2026-09-20 (Europe/Prague). Target: `KitchenSocketLeft`, IEEE `0xa4c138241e3de538`, stock `_TZ3000_b28wrpvx` / `TS011F` Router. **No second OTA is approved by this document.**

## Observed, not inferred

- Target-only `cli6` stock wrapper SHA-256: `b8c1d7a797a33936ac26f26305633b58369b0f409cda7d31e6291bc76851debe`; size 159858 bytes. Zigbee2MQTT read-only index check returned `update_available=true` and the exact image URL. This checks server eligibility, not acceptance by the stock firmware.
- Update transaction `bseed-ota-5d8f6e4b497543d985d45711fb3c2d53`, 2026-09-20 13:44:47–13:45:13 local time: initial progress 0%, then Zigbee2MQTT reported `Upgrade End` status `ABORT`. Response had `data:{}`. The original monitor missed this matching-transaction error; fixed in `helper_scripts/bseed_targeted_z2m_ota.py` and tested in `tests/test_bseed_targeted_z2m_ota.py` (commit `c43bb3bc`).
- The subsequent live device read returned relay ON, 0 W, stock Router identity, 236 V and LQI 168–172. The unsuccessful attempt did **not** establish a Client conversion. The interrupted local `ACTIVE_LOCK.json` still needs explicit reconciliation.
- Offline binary verification: the stock wrapper and native `cli6` are byte-identical from Zigbee OTA byte 56 onward; Telink subelement length, embedded image-size field, firmware magic and stored CRC-32 all check out. Published stock-to-PM-Router and experimental `cli4` stock wrappers also pass these structural/CRC checks. **Do not treat these checks as proof of stock-bootloader acceptance.**

## Plausible causes requiring discrimination

- Firmware acceptance rule: the stock OTA client may reject the new Client payload/type or first image data. A stock-facing outer OTA header and valid CRC do not establish acceptance by this stock firmware revision. We have no authenticated record of the exact historical HifiLeft direct-conversion transfer image.
- OTA data path/radio: configured `default_maximum_data_size=100` bytes and `image_block_response_delay=250` ms yielded a 1599-chunk/4-per-second initial estimate. Zigbee2MQTT documents a 50-byte default; actual blocks depend on the device request. The displayed initial 0% does **not** prove zero data blocks arrived. A smaller block size is a diagnostic variant, not a confirmed fix.
- Less supported at present: corrupt served image, wrong Zigbee target, or malformed OTA/Telink header; the hashes, identities, structural checks, and check response contradict these simple failure explanations.

## Required diagnostic gate before any repeat or two-step migration

1. Preserve the existing target's stock state and lock/log evidence; inspect the relay/load and any parallel OTA. Do not use the failed lock as permission to launch another OTA.
2. Capture a **target-scoped** Zigbee2MQTT debug trace of the next OTA exchange (`zhc:ota` and relevant device/ZCL messages) to establish the actual `queryNextImageRequest` parameters, offered image, `imageBlockRequest` offsets/max size, sent `imageBlockResponse` blocks and `upgradeEndRequest` status. Record first/last offsets; distinguish rejection before blocks from a stalled/repeated block exchange. The earlier info-level log does not contain this packet sequence.
3. Only after checking the trace, choose and document a single controlled variable: a safer 50-byte response limit for a radio-path hypothesis, a genuinely compatible image for an image-validation hypothesis, or the separately accepted published stock-to-Router route followed by a *separately evaluated* Router-to-Client update. Do not automatically retry the identical `cli6` wrapper.
4. Independently verify post-update role, software build ID, endpoint-2 relay ON, reporting, parent/rejoin and metrology; 100% OTA progress alone is insufficient.
