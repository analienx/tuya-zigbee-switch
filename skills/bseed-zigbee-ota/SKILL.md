---
name: bseed-zigbee-ota
description: Single-device, auditable Zigbee2MQTT OTA campaigns with post-flash hardware acceptance gates.
---

# BSEED Zigbee OTA maintenance

Use this skill for BSEED OTA flashes, transfer failures, role transitions, rejoin diagnostics and handoffs. Read `docs/bseed_targeted_ota_runner.md` and the target's latest evidence before any firmware write. Do not substitute conversational recollection for current hardware identity.

## Invariant: OTA transport is not firmware acceptance

OTA `status: ok`, 100% transfer and stock-facing outer `file_version` establish transfer completion only. Acceptance additionally requires actual installed firmware build, completed re-interview, correct Zigbee role, live endpoint reporting, relay/load safety, bindings, metrology and network stability. An old Router row in Zigbee2MQTT's database does not prove the stock firmware is running after a role change.

## Procedure

1. Confirm exact IEEE, model, manufacturer, board and stock OTA tuple; verify firmware provenance/hash and recovery path. Independently establish what the attached load is and whether power interruption is safe. Privately back up Zigbee2MQTT database, configuration and coordinator. Inspect **all** live OTA campaigns; a per-workdir lock is not network-wide.
2. Copy `docs/bseed_ota_profile.example.json` to a **private path outside git**. Populate exact target, image, SHA-256, native payload, stock-facing tuple, expected build/role, MQTT settings, workdir and one-entry index locations. Never commit credentials, private logs, images or backups.
3. Run `python helper_scripts/bseed_ota_campaign.py --profile PRIVATE.json --mode prepare` to build a private single-target index. Serve the index and image on the LAN, keeping the fleet OTA index unchanged. Run `--mode preflight`, then `--mode check`; inspect the response URL, identity, relay and target-specific lock.
4. Only with explicit authorization for the exact device, run `--mode flash --confirm-ieee 0xEXACTIEEE`. No automatic retry after `ABORT`, timeout, stale lock or failed preflight. Capture transaction-matched responses and block-level debug logs. Do not restart Zigbee2MQTT, coordinator, HA or MQTT during the transfer.
5. Run `--mode postflash` and review its private JSON evidence. Its `postflash_candidate` is not final acceptance: independently validate live relay behavior, client parent/rejoin, expected endpoint and PM reporting, existing bindings, physical connected load and long-term stability. Do not mark a campaign `postflash_accepted` merely because its OTA transport was successful.

## KitchenSocketLeft 2026-09-20 evidence (not accepted)

Stock-facing PM Client `cli6` with 100-byte maximum aborted at 0%. A 50-byte run stalled at offset 41150 and returned device-side `ABORT`. A 32-byte run resumed from the partial image, passed that offset and reported 100% with an OTA `status: ok` at 15:32:47 Europe/Prague. The **postflash active-endpoints interview failed at 15:34:57**, and an OnOff read timed out at 15:36:08. Zigbee2MQTT retained stale stock Router metadata with `interview_completed: false`. **The custom Client firmware is NOT yet hardware accepted.**

Do not automatically power-cycle, factory reset, permit-join, re-pair or send another OTA while the device's actual boot and network state is unknown. First capture passive Zigbee2MQTT events and verify whether the same IEEE ever reannounces/rejoins. Any physical recovery must be based on the attached appliance and board-specific recovery route, not an assumption that the relay will stay ON.

## Diagnosing future failures

Inspect *device-side* `Upgrade End` status, the coordinator's effective block-request wait, monitor deadline, actual request offsets, source block size, repeated offsets and remote send failures. A coordinator-side 10-minute timeout cannot extend a stock firmware client's own ~20-second repeated-block abort. A smaller block size can help a transfer but cannot prove payload compatibility or boot success. Preserve debug logs, rollback temporary log verbosity, and document hypotheses separately from observed facts.

At the start of each future session read the latest `main`, this skill, the device-specific investigation, private `ACTIVE_LOCK.json` and the most recent postflash evidence. Commit reusable tools, tests and nonprivate diagnostics; keep private IPs, credentials, firmware binaries and local backups out of git.
