---
name: bseed-zigbee-ota
description: Single-device, auditable Zigbee2MQTT OTA campaigns with post-flash hardware acceptance gates.
---

# BSEED Zigbee OTA maintenance

Use this skill for BSEED OTA flashes, transfer failures, role transitions, rejoin diagnostics and handoffs. Read `docs/bseed_targeted_ota_runner.md` and the target's latest evidence before any firmware write. Do not substitute conversational recollection for current hardware identity.

## Project identity and current Router canary

Call these BSEED-specific builds **Analienx BSEED firmware (based on Romasku)**. Attribute the underlying Romasku switch framework and preserve its license, links and generic device identity. The upstream-derived Z2M converter helper named `romasku` is not evidence that every build is an unmodified upstream release.

2026-09-20 KitchenSocketRight: one 32-byte `v8u5-rc1` Router OTA retry returned transport `status: ok` after a previous 50-byte-ceiling `ABORT`. The first postflash verifier ran **before** a target re-interview; its build was stale. One targeted re-interview subsequently returned `ok`, but Zigbee2MQTT still reported `v8u4`, with `state_relay: OFF` versus preflash ON and energy 0 versus 11.72 kWh. Its device identity and NWK/Router role remained intact. Therefore **hardware is unconfirmed**, the retry campaign lock remains `ota_transfer_ok_postflash_unverified`, and no new OTA, relay command, reset or provisioning is permitted as a consequence of transfer completion alone.

## Invariant: OTA transport is not firmware acceptance

OTA `status: ok`, 100% transfer and stock-facing outer `file_version` establish transfer completion only. Acceptance additionally requires actual installed firmware build, completed re-interview, correct Zigbee role, live endpoint reporting, relay/load safety, bindings, metrology and network stability. An old Router row in Zigbee2MQTT's database does not prove the stock firmware is running after a role change.

## Procedure

1. Confirm exact IEEE, model, manufacturer, board and stock OTA tuple; verify firmware provenance/hash and recovery path. Independently establish what the attached load is and whether power interruption is safe. Privately back up Zigbee2MQTT database, configuration and coordinator. Inspect **all** live OTA campaigns; a per-workdir lock is not network-wide.
2. Copy `docs/bseed_ota_profile.example.json` to a **private path outside git**. Populate exact target, image, SHA-256, native payload, stock-facing tuple, expected build/role, MQTT settings, workdir and one-entry index locations. Never commit credentials, private logs, images or backups.
3. Run `python helper_scripts/bseed_ota_campaign.py --profile PRIVATE.json --mode prepare` to build a private single-target index. Serve the index and image on the LAN, keeping the fleet OTA index unchanged. Run `--mode preflight`, then `--mode check`; inspect the response URL, identity, relay and target-specific lock.
4. Only with explicit authorization for the exact device, run `--mode flash --confirm-ieee 0xEXACTIEEE` for same-role updates or `--mode transition --confirm-ieee 0xEXACTIEEE` for cross-role OTA (requires a verified `join_via` Router in the private profile). No automatic retry after `ABORT`, timeout, stale lock or failed preflight. Capture transaction-matched responses and block-level debug logs. Do not restart Zigbee2MQTT, coordinator, HA or MQTT during the transfer.
5. For **every same-role OTA**, after exact transaction-matched transfer OK, automatically run one target-only `device/interview` via `helper_scripts/bseed_z2m_postota_reinterview.py` before provisioning, postflash PM audit, or acceptance. Require the exact IEEE/name/manufacturer/model, Router/Client role, completed interview, and **fresh matching software build** after the response. The profile-driven `flash` now runs this step by default. If interview succeeds but the build still differs, stop with private evidence and leave the campaign unverified; never interpret the date code, header version or OTA `status: ok` as a substitute for the build. A separately authorized `--mode reinterview --confirm-ieee EXACT` can resume the metadata step without reflashing; it never auto-retries the OTA. Do not perform repeated interviews indefinitely.
6. For a role change, automatically use a bounded router-scoped permit-join window and verify the device rejoined by the same IEEE. Check live ZDO role versus Zigbee2MQTT cached type; if stale, send one target-only `device/interview` and verify it updated. Do not remove/force-remove, factory reset or power-cycle for stale metadata. `--mode rejoin` and `--mode metadata` resume those phases without reflashing. Then run `--mode postflash` and review its private JSON evidence. Its `postflash_candidate` is not final acceptance: independently validate live relay behavior, client parent/rejoin, expected endpoint and PM reporting, existing bindings, physical connected load and long-term stability. Do not mark a campaign `postflash_accepted` merely because its OTA transport was successful.

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

## Serial multi-device PM verification (Client and Router)

For verification of a documented set of PM sockets, use `docs/bseed_pm_fleet_verification.md` and `helper_scripts/bseed_pm_fleet_audit.py` with a PRIVATE roster outside git. The runner audits targets serially and fails the aggregate result if even one device lacks fresh telemetry, a matching settings baseline, correct role/build or bounded reporting. It has no mutation mode. Preserve historical evidence, never substitute a passing Client check for Router acceptance, and never treat an all-candidate read-only result as approval for broad OTA rollout.

For PM Routers, a live scale/energy read on the verified Router canary returned `UNSUPPORTED_ATTRIBUTE`, including separate attempts for `seMetering.multiplier` and `currentSummDelivered`. Treat the legacy Router firmware's supported ZCL read contract as unresolved. Do not enable Client provisioning on a Router, edit its persisted divisors or repeatedly probe the production Router. A verified fixture load cycle and device-originated raw report/scale corroboration remain separate hardware gates.

## Shared PM core regression gate (Router and mains Client)

Before changing or distributing PM firmware, read `docs/bseed_pm_variant_matrix.md` and run `make bseed/pm-matrix` in a clean local Linux toolchain checkout. This runs common PM/ZCL/scaling/NVM tests, role-specific suites and builds both role images at one source SHA with separate OTA identities. The published Router v8u4 predates the Telink PM attribute registration fix; do not rebuild or relabel it as an updated release. Use only a separately versioned, verified Router candidate and a target-only canary before any promotion. Never apply Client-only reporting or cached scales to a Router automatically.

## Failed PM Router OTA: mandatory offline diagnostics (2026-09-20)

KitchenSocketRight v8u4 -> v8u5-rc1 OTA ended in device `ABORT` at 1.86%; its
PRIVATE campaign lock is `update_error`. Read
`docs/bseed_pm_router_v8u5_ota_abort_20260920.md` and
`docs/bseed_ota_abort_forensics.md` first. Use
`helper_scripts/bseed_ota_abort_forensics.py` offline with the original
single-transaction JSONL and actual installed Telink SDK constants. The
50-byte server ceiling is not the device's actual requested block size;
this SDK requests at most 48 bytes. An info-level progress stall cannot prove
a failed image offset, radio defect, firmware write failure or root cause.

The Client post-abort OTA timer recovery is now shared with PM Router source
in **a separately versioned rc2 candidate**, but only affects behavior *after*
an abort and cannot repair v8u4's ongoing download. Do not claim rc2 resolves
the initial ABORT. No follow-up flash, unlock, retry, relay change or restart
is authorized solely by a clean offline build; require independent raw
block-level/APS evidence, a new device/network eligibility decision and
exact single-target authorization. Keep rc1's validated hash immutable.

## Same-role retention and interview-freshness hardening (2026-09-21)

For a same-role OTA, the target-only post-OTA interview now verifies the original
campaign's target name/IEEE, OTA image SHA-256, transaction token, terminal
`status: ok` and reported response ID. A valid interview must produce both a
`successful` target interview event and a fresh, non-retained bridge/device
inventory observation after its request. Old retained inventory and a matching
old cached build do not suffice. `postflash_manufacturer` and
`postflash_model` in a PRIVATE profile may differ from the stock preflash
identity, but must match the actual postflash Zigbee identity exactly. No
second OTA or repeated interview is implicit on failure.

The same-role flash runner records a fresh preflash relay/energy snapshot
inside its private transaction lock. The postflash checker compares the
**designated** relay property (`state_relay` for BSEED PM Router) and physical
relay policy, and requires cumulative PM energy not to regress unexpectedly.
Missing baseline data, changed relay policy, a different relay state or an
energy reset leave the campaign `unconfirmed` pending independent evaluation.
A passing check still does not prove physical relay safety, meter calibration,
retained bindings or stable sleepy-child parenting. Historical campaign locks
predating baseline capture do not qualify for this new retention gate.

## Non-PM mains Client parent-loss/OTA eligibility gate (2026-09-21)

For `TS011F-BS` mains EndDevice canaries, read `docs/bseed_nonpm_cli5_keepalive_canary.md` before running an OTA. A retained Zigbee2MQTT `online` flag, one fresh relay state or a recent `last_seen` value is **not** a sustained-response gate. Use the exact private non-PM profile (`non_pm=true`, `require_pm=false`, `preflash_build` and `preflash_relay_physical_mode` pinned). Run `--mode link-gate`, then only after a PASS run read-only `--mode check`; a newly completed link gate **after** the successful check is required for an explicitly authorized same-role flash. Every failed/aborted probe invalidates previous pass evidence; a new/failed OTA check archives and invalidates the old check. Never use a different workdir to evade those gates.

If link probing fails, stop before OTA and inspect passive parent/neighbor evidence, live target errors and physical LED/circuit safely. If three link probes pass but OTA eligibility fails, distinguish a firmware OTA-query-service issue from general link loss; do not conclude that keepalive or any parent is the root cause. No automatic OTA retry, relay command, factory reset, power-cycle, re-pair, coordinator restart or global permit-join. Preserve the unchanged candidate image SHA, private canary evidence and independent physical safety review.

After independently verifying the private server/index and image hash, prefer `python helper_scripts/bseed_ota_campaign.py --profile PRIVATE.json --mode qualify` for a single serial **read-only** link → OTA-check → link qualification. It never submits a flash request and aborts on the first failed stage. Follow the documented exact-device physical/load and rollback review separately; a green qualification expires and cannot be converted into unattended OTA authorization.

BedroomSocketCabinetRight later on 2026-09-21: the separate first link gate, targeted OTA check (`update_available: true` for exact private `cli5-rc1`) and **new** second link gate all passed without flash. The earlier failed OTA request and relay GETs remain real intermittent-failure evidence; this temporary recovery does not prove the parent/root cause or `cli5-rc1` on hardware. The private candidate server was stopped and readiness timestamps expire; review the latest canary doc and private evidence before any new live operation. No same-role OTA transfer has been performed on this socket.

## Non-PM `cli5-rc1` preflash code review (2026-09-21)

Read `docs/bseed_nonpm_cli5_preflash_review_20260921.md` before resuming BedroomSocketCabinetRight. The same-role non-PM OTA runner and index `prepare` now reject a wrong embedded Telink startup flag, image version/length, type-0 sub-element length and native CRC, even if the configured SHA-256 matches. This is **packaging validation, not proof of safe boot**. The saved golden non-PM Router has a different OTA type and is NOT a validated over-the-air rollback if a Client does not boot/rejoin. A clip reportedly available for a distinct dead PM board is not proof of a working TLSR8258 SWire recovery path on this non-PM socket. Keep flashing blocked until its physical load and exact-board isolation/programmer/readback/recovery path are independently established; do not use `qualify` alone as authorization. Do not perform a reset or power cycle during code review.

### Non-PM hardware recovery gate is now executable

For `BedroomSocketCabinetRight` (non-PM Client `cli4` → `cli5-rc1`), read `docs/bseed_nonpm_cli5_hardening_20260921.md` and `docs/bseed_nonpm_recovery_evidence.example.json`. Both the profile-driven `flash` command and direct targeted runner refuse this exact non-PM transfer without an external, exact-IEEE recovery record, two matching independently captured complete flash readbacks, the expected Telink boot/config/build markers, 32-byte transfer limit and current explicit `--confirm-load-unplugged`. Offline package checks and the read-only `qualify` command are allowed without hardware evidence. These software validations only corroborate files and operator attestations; they do not prove an unbootable device can be recovered. NEVER copy a PM board's dump or accept a generic clip as evidence for this non-PM board. Never run the flash mode to test the gate against a live device; test it with mocked subprocesses instead.

### 2026-09-21 supersession: reasonable non-invasive non-PM canary

The earlier **mandatory disassembly/full-flash-readback** gate is superseded **only** for `BedroomSocketCabinetRight` `cli4`→`cli5-rc1` with exact IEEE/image/board/role. Read `docs/bseed_nonpm_cli5_noninvasive_canary_20260921.md` FIRST. Both OTA entry points now offer an explicit, exact-target no-disassembly path: preserve the mandatory physical appliance-unplugged confirmation, 32-byte OTA payloads, image integrity, fresh `qualify`, separate exact-IEEE confirmation, and an explicit acknowledgment that recovery if boot fails is NOT guaranteed. Stronger SWire/readback evidence remains an alternative, not required for this opted-in canary. Never apply this waiver to PM sockets, Router transitions, other builds, or unrelated targets; never infer physical load confirmation from MQTT or prior chat. The user's PM socket reportedly failed LONG AFTER running rather than on first boot; do not state it was a proven boot brick. TLSR8258 has alternating OTA slots but automatic rollback after a crash or parent loss is **not** demonstrated for this Zigbee SDK and binary. Do not modify boot-selection flash flags or ship new watchdog logic in `cli5-rc1` without separate spare-hardware validation.

## Paced server profile for sleepy EndDevice clients (2026-09-24 live evidence)

BedroomSocketCabinetRight (`_TZ3000_o1jzcxou`, sleepy EndDevice, NWK 24188) stalled twice on unpaced transfers: 32-byte blocks died at offset 24112 (~15%) and 48-byte blocks died at offset 47088 (~30%). Block size was not the variable. Log forensics showed perfect uplink (LQI 120-141, every request arriving) with a lost downlink `imageBlockResponse`: the Telink client waits ~5 s, retries once, then aborts quietly with no `upgradeEnd`, staying alive and publishing telemetry. Only a fresh notify cycle restarts it. Contributing server-side cause: full-speed responses (~6-10/s) colliding with Tuya `defaultRsp` traffic drove coordinator `MAC_BAD_STATE` (0x19) transmit refusals inside both stall windows.

A per-request paced profile (`image_block_response_delay: 1200`, `default_maximum_data_size: 48`, `image_block_request_timeout: 300000`) resumed from the partial image at offset 47136 and reached 90.4% (offset 143376) with zero `MAC_BAD_STATE`, surviving 30 s retry storms. A third quiet abort there (same signature: answered response, no further requests) coincided with a server-side `image_block_request_timeout` of only 300000 ms firing at 22:51:35; the device was alive and querying again by 22:52:11. A relaunch with identical pacing and `image_block_request_timeout: 1800000` resumed at 143376 and completed 100% at 23:04:43 with `upgradeEnd`, device announce at 23:04:53 (`285356047` → `285356048`), and a successful re-interview at 23:05:19. Lesson: size the request timeout for the paced transfer duration plus retry-storm margin (60000..3600000 ms supported); a longer timeout does not rescue an aborted client, it only avoids funeraling a slow-but-alive one — the resume always comes from relaunch, and the device keeps its partial image across sessions. Postflash follow-up 2026-09-25 resolved the stale build string as packaging, not boot failure: the device's own spontaneous queries report `fileVersion=285356048` (new code live), while `swBuildId` still reads `1.1.2-bseedcli4` (compile-time `VERSION_STR`, proven not NVM-backed in `basic_cluster.c`). The served image now carries `1.1.2-bseedcli5-rc1` at the same size and `FILEVER` — so two different binaries share OTA identity `285356048`. Treat any rebuild-under-same-identity as a hygiene violation: keep `rc1`'s validated hash immutable, and require `FILEVER`, `VERSION_STR` and build date to move together in every future candidate. Enforced mechanically by `helper_scripts/bseed_ota_identity.py` (`gate`, `suggest-next`, `emit-make-vars`, `check-index`) against `zigbee2mqtt/ota/bseed_identity.json`, wired as `make bseed/identity-gate` and at the end of all three `make_scripts/build_bseed_*.sh` flows: take release versions from `emit-make-vars` output, never by hand, and no image ships without a gate pass. This is byte-level enforcement; it complements the profile-level `bseed_socket_version_policy` map (campaign claims), it does not replace it. Both enforce the same board-wide rule: file versions rise across both roles of a board, and stock `0xFFFFFFFF` wrappers are transport identities, never release numbers. Transition wrappers (`from-router.ota`) stay unregistered by design. A re-flash no-ops on version comparison (`...048` == `...048`); do not force it. Z2M exposes pacing only per MQTT update request, not per device in configuration.yaml; a global `ota.image_block_response_delay` would slow the whole fleet, so keep pacing in the campaign profile, never in global config. `helper_scripts/bseed_targeted_z2m_ota.py --response-delay-ms` and profile key `response_delay_ms` (passed through by `bseed_ota_campaign.py`) implement it; both are covered by offline tests. This run used 48-byte blocks, deviating from this skill's 32-byte non-invasive-canary prescription; the prescription stands unchanged — pacing, not block size, is the evidenced fix, and any gate change remains a separate owner decision.

Firmware-side follow-ups for a later release train (none of these repair an in-flight transfer): send an honest nonzero `minimumBlockPeriod` in block requests so every server paces automatically; extend the block-response wait/retry budget and always emit `upgradeEnd(ABORT)` instead of wedging silently; keep polling aggressively during OTA so parent indirect frames never expire. Paced server OTA is the delivery vehicle for that fixed image; after the fleet carries it, pacing becomes a fallback. Do not flip mains sockets to Router role casually to dodge parenting: that is a topology decision with routing-load and rejoin consequences, use the existing role-transition machinery if it is ever proposed.
