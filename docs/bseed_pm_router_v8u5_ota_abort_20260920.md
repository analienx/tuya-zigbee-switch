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

## Offline SDK/transaction forensics (follow-up; no new OTA)

`helper_scripts/bseed_ota_abort_forensics.py` parsed the original PRIVATE campaign
JSONL and the actual Telink SDK `zigbee/ota/ota.h` from the local build toolchain.
Its PRIVATE result is `abort_forensics_offline_v1.json` in the failed campaign root.
The device returned `ABORT`; last recorded progress was 1.86% at 20:41:26,
followed 64.09 seconds later by terminal error. The existing information-level
log has **no individual block offsets, request sizes, image-block responses,
APS outcomes or flash-write acknowledgments**. It cannot locate a failed block.

This SDK's `OTA_IMAGE_MAX_DATA_SIZE=48`, block-response timeout is 5 seconds,
and retry limit is 10. The 50-byte Z2M request parameter was a *server ceiling*;
its value alone does not establish the actual device-requested or delivered size.
The SDK can send `Upgrade End / ABORT` after repeated block-response timeout;
the observed stall is compatible with this branch, not unique proof of it.
The Z2M archive recorded no second OTA during the target's transfer window.

The Client's earlier abort callback fix deferred a periodic OTA query to avoid
racing the SDK's shared timer. Router v8u4 lacked that **post-abort recovery**
change, which does not itself prevent an original block-response stall.
Shared Router/Client recovery was added for a **distinct, offline-only rc2**
candidate; do not rebuild the failed rc1 under its original version/hash.
Neither rc1 nor rc2 has passed KitchenSocketRight hardware acceptance.

The next authorization gate remains a bounded, target-only *raw OTA trace*
showing last requested offset, device-requested `maxDataSize`, returned
`dataSize`, duplicate offsets, request/response timestamps and APS delivery.
Separate device-side timeout, flash-write, image-validation and RF diagnoses;
none is established by the information-level log or by this code change.
Keep the failed campaign locked and preserve its independent Z2M backup.

## Controlled 32-byte retry and current acceptance state (2026-09-21)

KitchenSocketRight received the **same immutable `v8u5-rc1` image** in one
separate, authorized retry. The raw Zigbee2MQTT trace confirmed device block
requests of at most 48 bytes and actual response payloads of 32 bytes (18 on
the final block); the OTA service returned `status: ok` at 22:18:07 Prague time
on 2026-09-20. This supports testing conservative block sizes, but does **not**
prove 48-byte payloads caused the original abort. Preserve the original 50-byte
failure evidence and this retry's separate private raw trace and campaign lock.

A target-only re-interview returned `status: ok` and reported successful
interview with unchanged IEEE, NWK and Router role. Nevertheless, Zigbee2MQTT
still reported `1.2.5-bseedv8u4` on 2026-09-21, despite the rc1 OTA payload
containing `1.2.5-bseedv8u5-rc1`. The latest read-only state had
`state=ON`, `state_relay=OFF`, `energy=0` and plausible mains voltage;
before OTA the designated relay was ON and cumulative energy was 11.72 kWh.
Neither the running rc1 build nor retention of physical relay/energy behavior
is established. Keep the **retry** lock in
`ota_transfer_ok_postflash_unverified`; do not claim flash/hardware acceptance,
clear the lock, power-cycle, toggle the relay or authorize another OTA based
only on successful transport or interview. No new live firmware action was
performed during the 2026-09-21 automation hardening.

## 21 Sep follow-up: rc1 installed; metadata length defect; PM continuity open

After one physical upstream mains cycle, targeted raw Zigbee read responses from KitchenSocketRight `0xa4c138075cd16ed4` confirmed endpoint-1 `genBasic.swBuildId = 1.2.5-bseedv8u5-rc1` (0x4000, status 0) and `genOta.currentFileVersion = 302329869`. **The rc1 image is executing.** The old `v8u4` string in Zigbee2MQTT inventory survived another targeted successful interview because the 19-byte rc1 build string exceeds zigbee-herdsman's 16-byte Basic `swBuildId` limit; normal higher-level read results were `{}` although the raw device reply contained the complete rc1 string. Do not flash again to fix that display, do not claim failed OTA activation, and do not rewrite the live Zigbee2MQTT database.

Raw endpoint-2 OnOff = OFF, consistent with the preflash saved power-on setting OFF; switch logical endpoint-1 `state: ON` is separate. Raw endpoint-1 metering summation = 0, multiplier 1, divisor 1000. The preflash database *cache* had summation 1172 and divisor 100, but no independently captured preflash raw meter reading establishes actual energy continuity or historical scaling. Preserve original data; no automatic energy-counter injection, relay command, reset, or acceptance. The firmware build-ID guard is shared in `src/zigbee/basic_cluster.c` and requires a future uniquely versioned candidate with <=16-byte build ID; previously distributed images and OTA versions must not be rewritten.
