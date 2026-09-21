# Targeted BSEED OTA runner

`helper_scripts/bseed_targeted_z2m_ota.py` runs **one explicitly identified Zigbee2MQTT device**. It is an experimental maintenance tool, not an automatic fleet rollout. It does not commit OTA images, passwords, Zigbee backups, per-household settings, or runtime logs.

Requires Python 3, `paho-mqtt`, PyYAML, local access to an independently verified firmware file and to a local HTTP image server reachable by Zigbee2MQTT. Use an MQTT configuration file outside this repo and a private scratch `--workdir` for the OTA lock, index-check record and timestamped JSONL logs.

## Process

1. Save the Zigbee2MQTT configuration, database and coordinator backup privately. Verify the physical relay/load and recovery procedure. Ensure another OTA campaign is not in progress, including outside the local scratch lock.
2. For direct stock conversion, independently check the exact board, stock OTA manufacturer/image type and payload compatibility. Do not assume one successful custom Client on another socket proves stock compatibility.
3. Prepare a **private, one-entry OTA JSON index** matching the stock device's manufacturer, image type and wrapper image, with the correct `fileSize`, `sha512`, URL and version. Serve it alongside the image; do not modify `index_bseed.json` or use a general fleet index for a Client experiment.
4. Run `--mode preflight`, then `--mode check` with `--index-url` pointing at that one-entry JSON index. The read-only check must return `update_available: true` and the exact expected image URL.
5. Only after inspecting the check response, run `--mode flash` with the **same** device, image and SHA-256. The check record expires after 30 minutes. The program refuses identity/role mismatch, unexpected relay state, elevated/missing reported power, offline bridge, open permit-join, bad image/hash, or a conflicting local OTA lock.
6. Keep the image HTTP server and monitoring alive. A success response is only the OTA service result; separately verify the installed firmware build, successful re-interview, Client role, preserved IEEE, endpoint-2 relay, physical power-on behavior, endpoint-1 PM measurements, bindings, zero-load reporting and mesh/parent health. A failed or incomplete OTA must **not** be blindly retried.

## Arguments and limitations

Run `python helper_scripts/bseed_targeted_z2m_ota.py --help` for options. All device identifiers, expected preflash identity, firmware path, SHA-256, URL, MQTT config path, broker and private workdir are supplied via CLI; no household-specific values are built into the repository script. `--native-image` compares the OTA content from offset 56 to independently verify a stock-facing wrapper contains the intended custom payload.

The runner uses an exact IEEE/friendly-name pairing and a fresh, non-retained relay `/get` response; it never commands a relay change. It accepts a **matching OTA transaction with empty `data`** as a legitimate failure response, and ignores foreign transactions/targets. If an OTA was interrupted, check live device state and the Zigbee2MQTT logs before reconciling a stale lock. The local lock cannot detect OTA operations begun by other software; do not run simultaneous campaigns. A 0 W reading alone does not identify the physically connected appliance or guarantee safe power interruption.

**Conservative OTA transfer size:** The runner explicitly sets `default_maximum_data_size` **per flash request**, defaulting to **50 bytes** instead of inheriting a potentially higher Zigbee2MQTT global value (the KitchenLeft bridge was configured for 100 bytes). Override with `--max-block-bytes N` only for a justified diagnostic within Zigbee2MQTT's 10–100-byte limits. The 50-byte default is a risk reduction based on Zigbee2MQTT's documented device compatibility; it is **not evidence that block size caused KitchenSocketLeft's ABORT**. No OTA settings are modified globally by the runner.

## Profile-driven campaign (preferred for future sessions)

Read `skills/bseed-zigbee-ota/SKILL.md` first. Copy `docs/bseed_ota_profile.example.json` to a **private location outside this repository**, replace every placeholder with independently verified values and use a unique `workdir` for each campaign. Ensure `index_output` is inside the private LAN image-server root, not in git. The image file and index must be reachable by Zigbee2MQTT at their exact HTTP URLs; a profile alone does not start the HTTP server or back up a device.

```bash
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode prepare
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode preflight
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode check
# ONLY after explicit target authorization and a successful read-only check:
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode flash --confirm-ieee 0xEXACT_TARGET_IEEE
# The successful same-role flash already performs a target re-interview then postflash verification.
# If the transfer succeeded but re-interview did not confirm its build, resume WITHOUT reflashing:
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode reinterview --confirm-ieee 0xEXACT_TARGET_IEEE
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode postflash
python helper_scripts/bseed_ota_campaign.py --profile /private/target.json --mode status
```

**Mandatory same-role post-OTA re-interview:** `flash` now runs exactly one target-only Zigbee2MQTT `device/interview` **after successful OTA transfer and before any PM provisioning/audit or postflash checker**. `helper_scripts/bseed_z2m_postota_reinterview.py` checks the exact device IEEE/name/manufacturer/model, unchanged role and NWK address, interview success and freshly refreshed expected `software_build_id`. It writes an immutable private evidence JSON. If that build still disagrees, it exits nonzero and the lock remains `ota_transfer_ok_postflash_unverified`; **do not reflash automatically**. A successful OTA header/date alone is insufficient. `--mode reinterview --confirm-ieee EXACT` retries *only the interview*, never the firmware update, after independent device assessment. It is not a replacement for postflash, physical relay/load, binding or PM tests. The cross-role `transition` still uses scoped rejoin and its own metadata workflow.

`prepare` verifies image bytes, SHA-256, stock-facing OTA header/tuple, native payload identity (when supplied), the exact single matching template entry and HTTP image integrity before writing a private single-entry index. It refuses to overwrite a different existing private index. `preflight`/`check`/`flash` delegate to `bseed_targeted_z2m_ota.py` without reimplementing its safety logic. `flash` requires a second explicit exact IEEE. `postflash` independently uses a read-only, bounded MQTT observation, writes an immutable private JSON snapshot and returns nonzero if role/build/interview/live state cannot be verified. It **does not** restart, re-interview, pair, toggle or flash the device. MQTT state events and cached metadata alone cannot establish physical relay safety; independent hardware and functional checks are mandatory.

**Lock semantics:** A successful Zigbee2MQTT OTA response now yields `ota_transfer_ok_postflash_unverified` rather than `update_ok`; this remains a blocking state until a separately verified acceptance is explicitly recorded. Legacy `update_ok` locks also block new campaigns. A separate workdir is not a way to circumvent an unresolved previous OTA: inspect other campaign locks and live Zigbee2MQTT OTA activity before beginning anything. `status` only reads the current workdir's local lock and last check.

## Automated cross-role migration and metadata reconciliation (2026-09-20)

Use `--mode transition --confirm-ieee 0xEXACT` for a *new* authorized Router/Client role change, after profile `prepare`, `preflight`, read-only `check`, private backups and the network-wide OTA gate. The PRIVATE profile must set `join_via` to a verified neighboring Router and `join_seconds` (30–180); never use the normal same-role `flash` action for a role transition. The cross-role command submits one OTA, records transport completion, opens a router-scoped permit-join window, observes the same IEEE returning with the expected build, and closes the window. Then it conditionally refreshes Zigbee2MQTT's cached role by issuing **one targeted `device/interview`** if a fresh node descriptor disagrees with cached inventory. It finishes with independent read-only postflash checks; it does not mark physical or metering acceptance automatically.

A `--mode rejoin --confirm-ieee 0xEXACT` resumes the scoped join/metadata/postflash sequence after a separately completed transfer. `--mode metadata --confirm-ieee 0xEXACT` runs the non-destructive metadata check/conditional targeted interview on an already rejoined device. Evidence and locks remain private outside git. An interview which succeeds and updates `type` does **not** require Zigbee2MQTT device removal, factory reset or another physical pairing: that is what happened on KitchenSocketLeft at 16:00–16:05 Prague time. The live ZDO node descriptor is authoritative for current role, while the Zigbee2MQTT inventory must agree before treating metadata repair as complete.

If scoped rejoin fails, do not automatically delete/force-remove the device: the firmware may require a physical pairing gesture, and removal can erase the coordinator's record while leaving the device on the old network. Stop with private evidence and an explicit `manual_pairing_required`/`unconfirmed` recovery status; do not continue with another OTA. If targeted interview still leaves a stale role, retain the device record and investigate controlled `remove`/`keep_config`/`clear_cache` semantics and a verified physical recovery path separately. Recreate Zigbee bindings and reporting after a real Zigbee network reset only after comparing them to preflash backup; do not assume cached binds were preserved on the device.

## PM metering readiness after a cross-role OTA

For BSEED PM socket profiles set `"require_pm": true`. The profile-driven `postflash` check then fails closed if endpoint 1 lacks `haElectricalMeasurement.activePower` reporting at min<=10 s/max<=60 s/change<=5 W or if no **fresh, non-retained** standard-property PM MQTT state with plausible voltage/current/power/energy is seen. This check adds to—not replaces—the live role/build and interview gates. It does **not** verify calibrations, physical load changes, energy accumulation or raw Zigbee `attributeReport` provenance. Keep the complete `tests/live_bseed_pm_metering.py` canary mandatory for release acceptance.

KitchenSocketLeft illustrates why: after stock Router→Client conversion, the per-device PM multiplier/divisor cache and `activePower` reporting were absent. Fresh readings such as `voltage=24065`, `current=218` were displayed without scaling. A single target-only reporting configuration and subsequent `device/configure` both timed out, so **the live device is still uncorrected**. Do not fix these symptoms by copying another socket's binary/config, editing Zigbee2MQTT's live `database.db`, globally changing metering options, or repeating failed commands unattended. First restore reliable target-specific ZCL responses; then verify its actual scale attributes, configure endpoint-1 reporting and capture unsolicited corrected PM states.

## Fresh same-role interview and retained-state gate (2026-09-21)

An OTA `status: ok` first produces `ota_transfer_ok_postflash_unverified`.
The profile-driven same-role `flash` checks the exact target/image SHA-256 and
transaction-linked OTA lock, then performs **one** target-only interview. It
requires a successful interview event and fresh non-retained `bridge/devices`
message after the request, unchanged IEEE/NWK/role and exact expected build.
For stock-to-custom same-role conversions with a changed manufacturer or
model, set `postflash_manufacturer` and `postflash_model` to the target's
independently verified Zigbee identifiers in the PRIVATE profile; the original
`manufacturer`/`model` remain the preflash identity.

At preflight, the updater saves the selected relay state, energy and physical
relay policy in its private campaign lock. For a same-role postflash check,
this baseline and exact image hash are mandatory. The selected relay property
must remain unchanged, any recorded physical relay policy must agree, and PM
cumulative energy must not unexpectedly decrease. Missing historical baseline
means **unconfirmed**, not an automatic permission to reconstruct it from a
later state, rerun OTA or clear the lock. Independent physical load, retained
bindings, device-originated metering and network-parent acceptance are still
required before deploying the image elsewhere.

## Non-PM Client live reachability (2026-09-21)

For the explicit non-PM `TS011F-BS` EndDevice profile only, pin `preflash_build`, `preflash_relay_physical_mode`, `non_pm=true` and `require_pm=false`. The profile runner now supports `--mode link-gate`: three spaced read-only target GETs, requiring fresh matching client build/relay/policy/IEEE. The gate records private immutable evidence and invalidates any earlier pass on initiation or failure. The normal `--mode check` requires a recent passing gate; it archives the old `LAST_CHECK.json` before a new attempt so a failed check cannot leave an older flash permission. The subsequent `--mode flash --confirm-ieee EXACT` requires **another** fresh passing gate begun after the successful check. Evidence expires after 10 minutes; the underlying OTA check still expires after 30 minutes. All PM Router/Client power and role gates remain mandatory and unchanged.

This link gate establishes only a short run of MQTT communication, not ZCL request provenance, a confirmed parent relationship or long-term radio stability. Do not infer a firmware fix from the gate or assume that an OTA query failure is necessarily parent loss. For the physical recovery ladder and `cli5-rc1` canary evidence, consult `docs/bseed_nonpm_cli5_keepalive_canary.md`.
