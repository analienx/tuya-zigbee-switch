# Repository agent instructions

For Zigbee firmware flashing, OTA, device role changes, stock-to-custom conversions and recovery, **read `skills/bseed-zigbee-ota/SKILL.md` first** and follow its safety and evidence gates. Consult `docs/bseed_targeted_ota_runner.md` and the latest device-specific incident record before using live credentials or modifying hardware. Use local, profile-driven tooling rather than inventing ad hoc one-off scripts.

Do not interpret Zigbee2MQTT OTA `status:ok` or 100% as a confirmed boot. Never commit a household MQTT configuration, private network address, SSH key, OTA firmware binary, coordinator backup, private campaign profile or runtime trace. A new OTA cannot bypass a failed or unverified prior campaign's lock merely by switching to another workdir; check network-wide OTA activity and actual postflash state first.

## Canonical Home Assistant diagnostics and Zigbee device identification

For any live Home Assistant access, Zigbee2MQTT NWK/address mapping or route-error investigation, load the **single canonical** [Home Assistant read-only skill](https://github.com/analienx/config/blob/main/skills/home-assistant-readonly/SKILL.md) from `analienx/config` (main). It provides the existing SSH alias, a host-key-verified Paramiko fallback for Windows OpenSSH exit-255 failures, and the reusable `ha_readonly.py` live inventory helper. Keep implementation and credentials in the canonical location; do not copy the helper or SSH settings here. This does not authorize Zigbee firmware flashing, HA mutations or bypass of this repository's own safety/deployment rules.
