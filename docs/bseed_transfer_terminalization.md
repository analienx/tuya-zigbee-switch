# Completed OTA transfer vs. unresolved hardware acceptance (issue #72)

**Status: DRAFT, NOT LIVE-AUTHORIZED.** This design protects the BSEED Zigbee network's single-OTA ownership without falsely accepting a device after a successful transport.

## Concrete incident (11 October 2026)

A PM Client OTA transport completed on HifiLeft to `cli14`, but its postflash report is `unconfirmed` because the original energy reading was **0.75 kWh** and the observed reading is **0 kWh**, while the captured relay state changed **OFF → ON**. Those are material retained-setting and relay-safety discrepancies, not cosmetic bookkeeping. The original campaign still owns the shared network lock in phase `ota_transfer_ok_postflash_unverified`. KitchenLeft's next `cli16` candidate is distinct and must not inherit HifiLeft hardware acceptance.

The official Zigbee2MQTT unschedule operation for HifiLeft has succeeded. This removes scheduled future OTA work, not the completed transfer, not the BSEED campaign lock and not the failed postflash evidence. Zigbee2MQTT's `status: ok` by itself is never hardware acceptance.

## Transaction model

1. **One shared network authority** with a stable kernel-backed mutex across cooperating BSEED executors. A persistent `.mutex` path is intentionally *not* an O_EXCL stale-PID lock. A `.terminalizing` journal blocks new `acquire` calls after any interrupted terminalization.
2. **Exact source identity:** old owner token, network ID, device, IEEE, local candidate image SHA, matching original `ACTIVE_LOCK.json`, and exact successful Zigbee2MQTT transfer response reporting the target software build. Only `ota_transfer_ok_postflash_unverified` and `postflash_candidate` can enter terminalization.
3. **Independent evidence:** offset-aware, fresh (last five minutes) continuous >=90-second Zigbee2MQTT observation *after* the transfer ended, online bridge, closed permit-join, complete inventory, no active OTA transfers, and fresh installed IEEE/role/build/interview. A plain JSON assertion is **not** an independently verified observation. The collector must be reviewed separately before enabling live commit.
4. **Explicit authorization:** a *separate*, recent, named operator approval binds to the exact token/network/IEEE/image and explicitly authorizes `retire-completed-transfer-with-unaccepted-hardware`, never acceptance and never an OTA start. A distinct first-party execution route must be approved; an externally refused combined unlock+flash action is not a retry target.
5. **Immutable receipts:** write an exclusive private intent and fsync; atomically rename the original network authority to an archive preserving its exact bytes; write an exclusive immutable committed receipt recording `hardware_acceptance=unverified`, baseline vs observed metering and relay, findings, hashes and installed identity. Remove only the pending journal **after** the receipt is durable. Do not delete the original work lock or substitute an accepted phase.
6. **Crash behavior:** any failure after pending journal creation leaves that marker and blocks another campaign. No TTL, manual deletion or blind automatic unlock. Additional audited recovery handling is required before live commit is enabled.
7. **Future status:** `bseed_ota_campaign.py --mode status` reports the current network owner and separately validates/prints historical `HISTORICAL_UNACCEPTED_TRANSFERS` from archived receipts. A second OTA does not overwrite HifiLeft's failure.

## Current implemented boundaries

- `helper_scripts/bseed_network_mutex.py`: shared transaction mutex + journal acquisition fence.
- `helper_scripts/bseed_network_campaign_lock.py`: acquisition, updates and existing closure transitions serialized.
- `helper_scripts/bseed_transfer_terminalization.py`: read-only `--mode inspect`; strict evidence validator, journaled library transaction and historical receipt reader. **`--mode commit` intentionally refuses** because no independently trusted live Zigbee2MQTT OTA-active collector or approved execution route is provided by this PR. Calling the library's commit method directly is a test abstraction, **not** permission to do so on hardware.
- `tests/test_bseed_transfer_terminalization.py`: mock-data regression fixtures and filesystem failure simulations, to be run via GitHub-hosted Actions at the exact SHA.

## Remaining gates before live use

- Implement and independently review a *real* read-only Zigbee2MQTT observer that can attest to network-wide OTA inactivity, distinguish retained MQTT state from fresh observations, capture actual closed permit-join and device identity, and bind its raw evidence/hash. A missing or incomplete scan must fail closed.
- Complete audited, idempotent journal recovery (before and after atomic archival), disk durability tests, two-executor concurrency tests, and check old executors cannot race a new terminalization.
- Get CI green on the exact candidate SHA; separately review the source and the concrete Hifi evidence. Do **not** backfill energy or relay differences as accepted.
- Obtain explicit approval for the terminalization itself through a supported execution route. Independently verify KitchenLeft's live preflight, local image hash, actual attached-load safety and fresh Zigbee2MQTT availability before *any* supervised `cli16` OTA.

No current Hifi lock is released and no KitchenLeft firmware is transmitted by this draft.
