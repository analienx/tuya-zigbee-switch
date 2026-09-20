# Offline Telink OTA abort forensics (both PM roles)

After a failed OTA, **do not retry** or overwrite the failed campaign lock.
`helper_scripts/bseed_ota_abort_forensics.py` reads existing PRIVATE evidence;
it does not connect to MQTT, SSH, Zigbee or a live device and cannot unlock OTA.
Supply a single transaction's `ota_*.jsonl`; optionally supply a pre-existing,
**target-filtered** raw Zigbee2MQTT debug trace and the exact SDK `ota.h` used to
build the installed firmware. Keep every input and JSON output outside git.

```bash
python helper_scripts/bseed_ota_abort_forensics.py \
  --campaign-log /private/campaign/ota_TRANSACTION.jsonl \
  --sdk-header /private/toolchain/sdk/zigbee/ota/ota.h \
  --output /private/campaign/offline_forensics_NEW.json
```

Use `--raw-log /private/target-filtered-ota-debug.log` only if that trace was
actually captured; do not fabricate offset or packet acknowledgments from
progress percentages. Requested `maxDataSize`, server-side transfer ceiling,
prepared payload `dataSize`, APS/radio delivery and device flash writes are
different stages. A repeated request demonstrates a retry, not its cause.
The parser cannot establish a precise abort cause from Z2M information logs.

The installed Telink SDK examined on 2026-09-20 has 48-byte device-requested
maximum, 5-second image-block response timer and 10 retries. `ABORT` after a
progress stall is compatible with exhausting those retries but does **not**
prove it; correlated OTA block requests and replies are required. The Client's
scheduled post-abort OTA query recovery is separate from this timeout mechanism.
Any future diagnostic OTA requires distinct authorization, no competing OTA,
preflash relay/load and network checks, one fresh private profile, bounded
*target-only* debug logging, and restoration of the previous debug levels.
Do not change production Z2M global OTA parameters to troubleshoot one plug.
