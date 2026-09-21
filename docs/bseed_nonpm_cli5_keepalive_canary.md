# Non-PM Client `cli5-rc1` parent-keepalive canary

Target: `BedroomSocketCabinetRight`, board `OUTLET_BSEED_TS011F`, IEEE pinned privately. Current `cli4` was observed intermittently offline/online with red network LED blinking on 2026-09-21. One successful relay read is not a sustained connection test. Do not infer that the firmware is accepted or the parent is at fault.

The installed `cli4` predates the shared 60-second mains-Client MAC data-poll keepalive source change. `nonpm-keepalive` is an **opt-in, distinct** `1.1.2-bseedcli5-rc1` / `0x11023010` build using the existing 65026 Client image type and unchanged hardware configuration. Its compiled role remains mains Rx-on EndDevice; BDB parent-loss rejoin/backoff remains enabled. The normal `nonpm` `cli4` build is preserved for reproducibility. It is not a PM image and must not be published in the fleet OTA index.

Before targeted OTA: verify exact IEEE, non-PM model/manufacturer, currently running `cli4`, parent link and intended physical load; save relay configuration, bindings and Zigbee2MQTT backup; inspect all active OTAs. Verify candidate SHA-256 and same-role OTA identity, the hardware-accepted non-PM Router rollback and the private campaign lock. If any check fails, do not flash.

During a separately authorized one-target OTA capture raw block logs and do not change the relay, coordinator, network channel or global Zigbee2MQTT settings. On a successful transfer perform one targeted re-interview, verify installed `cli5-rc1` and exact role/identity, then compare relay/power-on/indicator settings and bindings against baseline. Do not accept the image from an OTA `status: ok` alone.

Hardware acceptance needs repeated fresh relay reads, stable online/lastSeen, no recurring parent-loss LEDs, observed parent and automatic recovery after a **safe, independently controlled** parent interruption, plus no regression to nearby Zigbee devices. Do not switch or unplug a production parent just for a test. Preserve `cli4` and the golden non-PM Router rollback artifacts. If parent recovery still fails, investigate the parent/radio path and SDK poll behavior before altering the retry timer or hiding the LED.

### Non-PM OTA preflight compatibility

The generic targeted OTA runner normally requires a fresh numeric `power` field to enforce the PM load ceiling. This TS011F-BS Client has no metering sensor. Its private campaign profile must explicitly set `"non_pm": true`, `"require_pm": false`, `"relay_get_key": "state_relay"`, and the exact preflash `model`/`manufacturer`/`role` values `TS011F-BS`/`o1jzcxou`/`EndDevice`. The runner then requires the fresh MQTT device IEEE and relay state but exempts **only this precise non-PM variant** from the power check; PM and all other image classes still fail closed if numeric metering is absent. An unexpected `power` property also blocks this non-PM exception.

Use only the targeted one-entry OTA index and bounded block size. Preserve the exact `0fc6a7ed` firmware-source manifest and SHA-256 when host-side deployment scripts change independently; never rebuild/relabel an existing candidate binary under the same version. Record the actual connected appliance/load and user-facing power interruption risk before OTA. Do not use a new campaign directory to ignore a prior unresolved lock.
