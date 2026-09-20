# Targeted BSEED OTA runner

`helper_scripts/bseed_targeted_z2m_ota.py` runs **one explicitly identified Zigbee2MQTT device**. It is an experimental maintenance tool, not an automatic fleet rollout. It does not commit OTA images, passwords, Zigbee backups, per-household settings, or runtime logs.

Requires Python 3, `paho-mqtt`, PyYAML, local access to an independently verified firmware file and to a local HTTP image server reachable by Zigbee2MQTT. Use an MQTT configuration file outside this repo and a private scratch `--workdir` for the OTA lock, index-check record and timestamped JSONL logs.

## Process

1. Save the Zigbee2MQTT configuration, database and coordinator backup privately. Verify the physical relay/load and recovery procedure. Ensure another OTA campaign is not in progress, including outside the local scratch lock.
2. For direct stock conversion, independently check the exact board, stock OTA manufacturer/image type and payload compatibility. Do not assume one successful custom Client on another socket proves stock compatibility.
3. Prepare a **private, one-entry OTA JSON index** matching the stock device's manufacturer, image type and wrapper image, with the correct `fileSize`, `sha512`, URL and version. Serve it alongside the image; do not modify `index_bseed.json` or use a general fleet index for a Client experiment.
4. Run `--mode preflight`, then `--mode check` with `--index-url` pointing at that one-entry JSON index. The read-only check must return `update_available: true` and the exact expected image URL.
5. Only after inspecting the check response, run `--mode flash` with the **same** device, image and SHA-256. The check record expires after 30 minutes. The program refuses identity/role mismatch, unexpected relay state, elevated/missing reported power, offline bridge, open permit-join, bad image/hash, or a conflicting local OTA lock.
6. Keep the image HTTP server and monitoring alive. A success response is only the OTA service result; separately verify the installed firmware build, successful re-interview, Client role, preserved IEEE, endpoint-2 relay, physical power-on behavior, endpoint-1 PM measurements, bindings, zero-load reporting and mesh/parent health. A failed or incomplete OTA must **not** be blindly retried.

## Arguments and limitations

Run `python helper_scripts/bseed_targeted_z2m_ota.py --help` for options. All device identifiers, expected preflash identity, firmware path, SHA-256, URL, MQTT config path, broker and private workdir are supplied via CLI; no household-specific values are built into the repository script. `--native-image` compares the OTA content from offset 56 to independently verify a stock-facing wrapper contains the intended custom payload.

The runner uses an exact IEEE/friendly-name pairing and a fresh, non-retained relay `/get` response; it never commands a relay change. It accepts a **matching OTA transaction with empty `data`** as a legitimate failure response, and ignores foreign transactions/targets. If an OTA was interrupted, check live device state and the Zigbee2MQTT logs before reconciling a stale lock. The local lock cannot detect OTA operations begun by other software; do not run simultaneous campaigns. A 0 W reading alone does not identify the physically connected appliance or guarantee safe power interruption.
