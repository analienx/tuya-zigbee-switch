# TS0505B preflash contract (experimental; not a release authorization)

This target is not flash-ready. The current D0 binary is a dark diagnostic reference: its physical RGB+CCT output remains a weak no-op. A valid Zigbee OTA header, a reproducible Silicon Labs build or a successful stock-client Image Block request cannot establish production-board compatibility or bootloader acceptance.

## Offline verification

From the unified repository root:

```sh
python -m helper_scripts.ts0505b.preflash
python -m helper_scripts.ts0505b.preflash --artifact /local/private/candidate.ota
python -m helper_scripts.ts0505b.preflash --artifact /local/private/candidate.ota --require-ready
```

The optional artifact is processed entirely offline; the tool never connects to Zigbee2MQTT or a device. It checks SHA-256 and length against the frozen D0 manifest, exact stock-facing OTA identity, outer version, OTA header and single embedded GBL, internal version/security flags, allowed tags, and exact programmed flash ranges. A conservative reference range bound is **not** evidence of the installed bootloader or NVM layout.

`--require-ready` fails while any required independent evidence is absent, the candidate is marked non-deployable, or physical output is not verified. A syntactically valid candidate never automatically changes those states.

## Evidence required before a lighting-capable first flash

- Independently establish the installed module/SoC, flash density, application, OTA staging, token/NVM and bootloader boundaries. Family reference addresses do not prove production layout.
- Establish the stock bootloader's signature/encryption, secure-boot and rollback requirements, plus its internal application version. Basic `ApplicationVersion=112` and outer OTA version `0x10003607` are different metadata fields; neither proves the Gecko `ApplicationProperties` version.
- Resolve stock-to-custom pre-byte acceptance without transmitting image data; then prove the exact candidate's verification policy and a viable recovery path before an authorized transfer.
- Verify production PCB output routing and electrical behavior (PWM polarity/frequency, per-channel limits, reset state, brightness and RGB+CCT behavior). The Tuya ZSU reference pin map is not a measured production-board contract.
- Freeze the final, lighting-capable build and run byte-level preflight on **those actual bytes**. A dark D0 diagnostic artifact is not the finished image.

Silicon Labs permits bootloader configurations that require signed and/or encrypted upgrades and optionally enforce rollback. Default settings in example projects cannot be assumed for an OEM-installed bootloader: https://docs.silabs.com/zigbee/latest/ota-bootload-server-client-setup-zigbee-sdk-v7x-higher/07-advanced-topics

TuyaOS MG21 reference documentation: https://github.com/joliam/tuyaos_zigbee_8258/blob/80a703674b00e0155293484f2ef25b063743ed07/docs/TuyaOS_Zigbee_SDK_User_Guide/en/1.Introduction/4.Key_Parameters.html

## Decision boundary

Do not publish a deployable OTA index or send firmware bytes while this gate is red. The pre-byte metadata probe is a separate, limited transport test; reaching block 0 would only verify the stock Zigbee OTA client's metadata acceptance, not verification, bootloading, LED operation or recoverability. Physical flashing/OTA transfer requires fresh, explicit authorization after all applicable checks.
