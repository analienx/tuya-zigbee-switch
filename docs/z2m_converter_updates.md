# Zigbee2MQTT converter deploys and upgrades

The custom converter (`zigbee2mqtt/converters/switch_custom.js`) runs inside the live
Zigbee2MQTT add-on, against the zigbee-herdsman-converters (ZHC) and zigbee-herdsman
versions that add-on bundles. CI tests it against the versions pinned in
`tests/zhc_runtime/package.json`. Keep the two equal.

## Deploying a converter change

1. Merge the change and let the `test` workflow on `main` finish. Download its
   `z2m-converter-canary-<sha>` artifact.
2. Run `helper_scripts/z2m_converter_deploy.py --artifact-dir <artifact> --site <private site.json>`.
   This is a dry run. It refuses when:
   - the artifact differs from its CI `SHA256SUMS` or from the committed converter;
   - any BSEED OTA lease is held (a restart kills transfers);
   - the running ZHC or herdsman version differs from the CI pins.
3. Repeat with `--apply`. The helper:
   - backs up the deployed file (`.pre-deploy-<UTC>`) and installs the new one;
   - restarts the add-on once and checks the latest start log for
     `Loaded external converter 'switch_custom.js'` and `Zigbee2MQTT started!`;
   - restores the backup and restarts again if either line is missing.

## Upgrading Zigbee2MQTT

1. Find the ZHC and herdsman versions the new release bundles (its `package.json`).
2. Open a PR that sets `tests/zhc_runtime/package.json` to those versions and
   regenerates the lockfile (`npm install --package-lock-only --ignore-scripts`).
   Merge only when CI is green.
3. Upgrade the add-on only while no OTA lease is held and after a Zigbee2MQTT backup.
   Add-on auto-update stays off.
4. Redeploy the converter from the new `main` artifact with the helper; the version
   check now passes. Resume campaigns afterwards.

## OTA offers

The live add-on runs with `ota.disable_automatic_update_check: true` and no
override index. Never point `zigbee_ota_override_index_location` at upstream
romasku's `index_router-FORCE.json`. Its `b28wrpvx` (43556), `o1jzcxou` (43555)
and `iedhxgyi` (45577) entries carry file version `0xFFFFFFFF`, so every custom
BSEED Router would be offered upstream generic firmware. Only the reviewed
`zigbee2mqtt/ota/index_bseed.json` is safe to use as an override.
