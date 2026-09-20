# Repository agent instructions

For Zigbee firmware flashing, OTA, device role changes, stock-to-custom conversions and recovery, **read `skills/bseed-zigbee-ota/SKILL.md` first** and follow its safety and evidence gates. Consult `docs/bseed_targeted_ota_runner.md` and the latest device-specific incident record before using live credentials or modifying hardware. Use local, profile-driven tooling rather than inventing ad hoc one-off scripts.

Do not interpret Zigbee2MQTT OTA `status:ok` or 100% as a confirmed boot. Never commit a household MQTT configuration, private network address, SSH key, OTA firmware binary, coordinator backup, private campaign profile or runtime trace. A new OTA cannot bypass a failed or unverified prior campaign's lock merely by switching to another workdir; check network-wide OTA activity and actual postflash state first.
