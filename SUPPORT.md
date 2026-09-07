# Support

Before asking for help, verify that your device matches an explicitly supported identity in [README.md](README.md) or [docs/supported_devices.md](docs/supported_devices.md).

## For BSEED devices

Include these details in any bug report or support request:

- Zigbee manufacturer and model identity;
- firmware build ID and file version;
- exact board/configuration string if known;
- Zigbee2MQTT/ZHA environment and coordinator type;
- what operation failed (pairing, OTA, relay, metering, configuration, etc.);
- relevant logs around the failure;
- whether the device started from stock Tuya firmware or custom firmware;
- final relay/power state after the test.

For PM issues, distinguish between:

- numeric `0` values at true no-load;
- `null`/missing/stale values;
- nonzero load with zero measurement;
- converter/test-harness errors versus actual device-originated Zigbee errors.

## Stock-to-custom conversion

Do not try a conversion image on a merely similar-looking device. The documented wrappers are identity-specific.

A successful stock→custom path does not imply that stock firmware can later be restored. If recovery matters, obtain and validate a full original-firmware restore path before converting the device.

## Logs and privacy

Trim logs to the relevant time window. Remove Wi-Fi credentials, tokens, private URLs, unrelated device identifiers, or other secrets before posting them publicly.

## Feature requests

Describe the user-facing problem first, then the proposed behavior. For hardware-specific features, include the exact target identity and explain whether the change belongs in the shared core or behind a target guard.
