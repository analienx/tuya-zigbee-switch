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
4. Only with explicit authorization for the exact device, run `--mode flash --confirm-ieee 0xEXACTIEEE` for same-role updates or `--mode transition --confirm-ieee 0xEXACTIEEE` for cross-role OTA (requires a verified `join_via` Router in the private profile). No automatic retry after `ABORT`, timeout, stale lock or failed preflight. Capture transaction-matched responses and block-level debug logs. Do not restart Zigbee2MQTT, coordinator, HA or MQTT during the transfer.
5. For a role change, automatically use a bounded router-scoped permit-join window and verify the device rejoined by the same IEEE. Check live ZDO role versus Zigbee2MQTT cached type; if stale, send one target-only `device/interview` and verify it updated. Do not remove/force-remove, factory reset or power-cycle for stale metadata. `--mode rejoin` and `--mode metadata` resume those phases without reflashing. Then run `--mode postflash` and review its private JSON evidence. Its `postflash_candidate` is not final acceptance: independently validate live relay behavior, client parent/rejoin, expected endpoint and PM reporting, existing bindings, physical connected load and long-term stability. Do not mark a campaign `postflash_accepted` merely because its OTA transport was successful.

## KitchenSocketLeft 2026-09-20 evidence (not accepted)

Stock-facing PM Client `cli6` with 100-byte maximum aborted at 0%. A 50-byte run stalled at offset 41150 and returned device-side `ABORT`. A 32-byte run resumed from the partial image, passed that offset and reported 100% with an OTA `status: ok` at 15:32:47 Europe/Prague. The **postflash active-endpoints interview failed at 15:34:57**, and an OnOff read timed out at 15:36:08. Zigbee2MQTT retained stale stock Router metadata with `interview_completed: false`. **Historical at this stage:** the subsequent scoped join succeeded, the custom Client build booted, a live ZDO node descriptor reported `EndDevice`, and a targeted interview updated Zigbee2MQTT cached `Router` to `EndDevice`. Full relay/meter/binding/stability hardware acceptance remains open.

Do not automatically power-cycle, factory reset, force-remove or send another OTA while the device's actual boot and network state is unknown. Permit-join is allowed only by a bounded, verified Router-scoped role-transition recovery step, with the joining window closed and logged afterwards. First capture passive Zigbee2MQTT events and verify whether the same IEEE ever reannounces/rejoins. Any physical recovery must be based on the attached appliance and board-specific recovery route, not an assumption that the relay will stay ON.

## Diagnosing future failures

Inspect *device-side* `Upgrade End` status, the coordinator's effective block-request wait, monitor deadline, actual request offsets, source block size, repeated offsets and remote send failures. A coordinator-side 10-minute timeout cannot extend a stock firmware client's own ~20-second repeated-block abort. A smaller block size can help a transfer but cannot prove payload compatibility or boot success. Preserve debug logs, rollback temporary log verbosity, and document hypotheses separately from observed facts.

At the start of each future session read the latest `main`, this skill, the device-specific investigation, private `ACTIVE_LOCK.json` and the most recent postflash evidence. Commit reusable tools, tests and nonprivate diagnostics; keep private IPs, credentials, firmware binaries and local backups out of git.

## Mandatory metering gate for BSEED PM sockets

A PM Client rejoin and successful interview do **not** establish working metering. For the private PM socket profile set `require_pm=true` and run `--mode postflash` with the strict PM gate. Verify endpoint-1 `activePower` min<=10 s/max<=60 s/change<=5 W, actual endpoint-1 coordinator binding, the target's read multiplier/divisor values, plausible fresh standard `power/current/voltage/energy` and a genuine device-originated unsolicited report. The `postflash_candidate` result is still not full hardware acceptance; use the independent loaded-to-zero test described in `docs/bseed_pm_e2e_acceptance.md`.

If standard readings are obviously unscaled or reporting configuration fails, preserve the exact response and mark the target `unconfirmed`. A live ZDO reply does not imply ZCL meter commands work. Do not repeatedly invoke `device/configure` on an intermittently reachable socket, change relay state, manually edit Zigbee2MQTT's live database, or silently copy another socket's scale attributes. Restore reliable communication and verify the target's own cluster response before attempting one bounded targeted PM configuration.

## PM Client deployment automation (implemented after cli6 KitchenLeft canary)

Read `docs/bseed_pm_client_deployment.md` before a PM socket rollout. `helper_scripts/bseed_pm_provision.py` audits a single pinned custom PM device without changing its state by default; the `--apply --confirm-ieee EXACT` mode configures missing endpoint-1 metering reports one at a time, records private before/after evidence, and fails closed on errors. It checks live ZDO role, pinned model/build, bridge availability, device-specific cached scale constants, and coordinator bindings. Target-only `device/configure` for missing scale/binds requires the separate `--allow-configure` flag; repeated or unlimited configure retries are forbidden. Its result is only a PM configuration *candidate*, never proof of accurate physical metering, relay safety or stable parent connectivity.

The campaign offers `--mode provision-pm --confirm-ieee EXACT` for independently resumable PM setup on already-flashed devices; an authorized cross-role `transition`/`rejoin` now runs provisioning after metadata refresh and before the postflash checker. Populate PM SSH host/key, bounds and optional known-idle flag in the PRIVATE profile. `--expect-idle` fails if either power or current remains nonzero; do not clamp unverified noise or reset cumulative energy. Use the independent controlled-load-to-zero fixture and HA state checks in `docs/bseed_pm_e2e_acceptance.md` before promoting any image to another socket.

KitchenSocketLeft live 2026-09-20: all four endpoint-1 report rules were installed successfully with target-only reporting/configure; `activePower` was independently read back at 10/60/5. The strict no-load check **still failed** and a relay endpoint-2 read timed out after a reconnect. Its campaign lock remains `ota_transfer_ok_postflash_unverified`; do NOT infer that flashing and broad deployment are approved. Preserve private canary evidence and require independently fresh raw report, true loaded-to-zero transition and stable networking before another PM candidate.

KitchenSocketLeft later read-only observation (2026-09-20): three fresh non-retained zero/zero power/current MQTT samples were seen over 85 s, then an 85 s `--require-pm` verifier returned `postflash_candidate` with a fresh live ZDO descriptor. Distinguish this *candidate* gate from physical load-to-zero and long-term parent/relay acceptance. Because current max-report is 300 s, strict `--expect-idle` provisioning requires a full reporting window plus margin, never a premature stale MQTT shortcut.

## Router/Client PM verification and settings persistence

Before authorizing a PM Router or mains Client release, consult `docs/bseed_pm_role_verification.md` and run `--mode audit-pm` with an exact per-device, private campaign profile. Audit and compare the original settings snapshot after each intended image update, role transition, rejoin, and controlled test-power recovery; no prechange snapshot means persistence is **not verified**. This audit does not reconfigure devices or command their relays.

Router `v8u4` has legacy cached voltage/energy scale conventions and 3600-second reporting intervals; **never run the Client-only provisioner on a Router** or copy Client calibration/scales to it. Existing Router `audit-pm` failures require diagnosis and a validated Router-specific fix, not a Client profile or bypass of `require_pm`. Preserve Router child-parenting, route/rejoin functionality and appliance settings. A firmware or metadata match never supersedes fixture-controlled load-to-zero and sustained network gates.
