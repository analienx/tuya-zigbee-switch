# BSEED PM Router v8u5-rc1 targeted OTA canary: device ABORT (2026-09-20)

**Disposition: transfer FAILED, hardware candidate NOT ACCEPTED; no repeat OTA authorized.**
The one-device canary was KitchenSocketRight, a `TS011F-BS-PM` Router running
`1.2.5-bseedv8u4` (`302329863`). The intended candidate was
`1.2.5-bseedv8u5-rc1` (`302329869`) from the clean shared Router/Client
build matrix at `2977fac3`. The Router OTA was 196,098 bytes and matched the
matrix SHA-256. The candidate was *not* applied or validated on hardware.

A private one-entry OTA index and local HTTP endpoint were verified from the
Zigbee2MQTT host. A private backup of the Z2M configuration, database and
coordinator was taken before updating. The target's endpoint-2 `state_relay`
GET returned ON/0 W; generic `state` is the wrong getter for this device.
The OTA check matched exactly the intended device, image URL, and new version.

Only one OTA request was sent, with `default_maximum_data_size=50` bytes.
Zigbee2MQTT reported 1.03%, then 1.59%, then **1.86%**, with sharply increasing
estimated remaining times. At 2026-09-20 20:42:30 Europe/Prague it returned
`status:error`, `reason: ABORT` for the original transaction. The runner wrote
`ACTIVE_LOCK.json` phase `update_error` and did not retry or run postflash
provisioning. This is a device-side abort reported through Z2M, **not** a
confirmed OTA transfer, firmware boot, or evidence the new code is broken.

After the abort, the live Z2M inventory still identified the same device as
Router `1.2.5-bseedv8u4`, interview successful. A fresh read-only
`state_relay` response returned ON, 0 W, 0 A and 238 V. Read-only comparison
against previous private settings snapshots found **zero recorded settings
changes**, and Router PM reporting remained unchanged. These are Z2M record
and live-response observations, not proof of flash integrity, full Router
child-parenting, or independently calibrated PM measurements.

**Unresolved:** this log does not reveal the exact failed image offset,
last device-originated block request, flash write response, or OTA-client
internal abort reason. The slow progress could have multiple causes; do not
assert image incompatibility, RF congestion, block size, or a specific timer
bug solely from this evidence. Obtain bounded raw OTA/ZCL debug evidence and
review the installed `v8u4` Telink OTA client, transport retry/deadline,
repeated offset and bootloader geometry *offline* before authorizing a new
attempt. Existing `v8u4` PM read-attribute and reporting issues remain open.

Keep private `ACTIVE_LOCK.json`, JSONL logs, matrix evidence and Z2M backups
outside git; do not change the lock to accepted, automatically retry, clear
Zigbee device records, force a rejoin, modify reporting or restart coordinator.
A later update requires a separately justified one-device campaign and fresh
hardware/mesh preflight; a successful OTA alone is not hardware acceptance.
