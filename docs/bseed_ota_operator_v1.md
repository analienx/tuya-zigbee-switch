# BSEED OTA operator v1.0.0 — supervised runbook

**Status:** Review candidate on `fix/bseed-ota-workflow-v1-20260923` until hosted CI and the PR review complete. This script is an operator around the existing `bseed_ota_campaign.py`, not a firmware release or a Zigbee-wide generic flasher. It does not modify any production device merely by being committed.

## Existing code ownership

`helper_scripts/bseed_targeted_z2m_ota.py` owns image/header validation, target identity, relay, MQTT OTA requests, transaction-matched responses, local OTA lock and timeout behavior. `helper_scripts/bseed_ota_campaign.py` owns the profile-driven phases, OTA qualification, recovery gate and postflash one-target interview. `helper_scripts/bseed_nonpm_link_gate.py` owns fresh three-sample non-PM link evidence. The **new** `helper_scripts/bseed_ota_workflow.py` v1.0.0 orchestrates these components; it does not implement a second OTA protocol or silently waive any gate.

## Private inputs and preconditions

Supply a fully reviewed private profile outside git with one exact IEEE, source firmware image and SHA-256, wrapper/header details, one-entry index/template, broker/MQTT paths and an unused `workdir`. The `--campaign-root` must be a private directory containing the **known campaign locks for this coordinator** and enclosing the new workdir. Back up live Zigbee2MQTT configuration, device database, bindings, reporting and coordinator first. The scan cannot see firmware transfers initiated by other tools or campaign folders outside the declared root: inspect live Zigbee2MQTT state and any external jobs separately. A successful link gate is not proof of sustained parent stability. A prior exact-IEEE `update_error` needs log review, verified live state and `--ack-prior-failures` on qualification and flash; never edit or erase old locks.

Use a **different** private workdir and profile on each new attempt; the script refuses to reuse any workdir containing an `ACTIVE_LOCK.json`, even one marked accepted. A separate workdir is for immutable evidence, not permission to bypass failed/unverified campaigns.

## Commands (replace placeholders; never commit private parameters)

```shell
python helper_scripts/bseed_ota_workflow.py --version
python helper_scripts/bseed_ota_workflow.py --profile PRIVATE.json --campaign-root PRIVATE_ROOT --mode audit
# Run in its own terminal, keeping it open for the full transfer:
python helper_scripts/bseed_ota_workflow.py --profile PRIVATE.json --campaign-root PRIVATE_ROOT --mode serve --bind PRIVATE_URL_HOST
# Qualifies one device; safe to re-run with a fresh workdir if the old check expired:
python helper_scripts/bseed_ota_workflow.py --profile PRIVATE.json --campaign-root PRIVATE_ROOT --mode qualify --ack-prior-failures
# Only after exact-target authorization and fresh physical/load/recovery confirmation:
python helper_scripts/bseed_ota_workflow.py --profile PRIVATE.json --campaign-root PRIVATE_ROOT --mode flash --confirm-ieee EXACT_IEEE --ack-prior-failures --confirm-load-unplugged --accept-nonrecoverable-ota-risk
```

The last two flags are required only for the exact non-PM risk-exception case, not generic PM Routers, dimmers or other hardware. Physical `unplugged` means no appliance connected to the socket, **not** relay OFF or a 0 W MQTT reading. If the image/URL/role/profile changes, discard the old qualification and start a new reviewed profile.

`audit` reports known local locks without acknowledging them, starting OTA or writing a new check. `serve` binds only to the profile's exact private URL host/IP and serves only the pinned image and index paths; an existing server with identical image bytes is left alone. The server can be restored while an OTA is active without clearing its lock. If a port is already occupied by a mismatched service, the command fails rather than killing it. Start serving before `qualify`; the index can be created after the server is up.

`qualify` verifies image SHA-256, full native/wrapper/OTA structure via canonical `prepare`, exact served HTTP image and index contents, one-target `preflight`, and the canonical non-PM link → check → NEW link sequence (PM profiles use their separate canonical check). It does **not** transfer firmware. Qualification evidence expires under the canonical runner's rules. `flash` repeats this qualification immediately before submitting exactly one OTA request, then delegates to the canonical flash and postflash steps. There is **no auto-retry** after timeout, ABORT, incomplete transfer, failed interview, relay mismatch or stale build.

After transfer, require exact fresh firmware build, same permanent IEEE and intended role, targeted interview, saved settings/bindings/reporting, physical relay/button/load and sustained network health. Do not equate OTA 100%/`status: ok` with firmware acceptance. A failed campaign remains locked. A private HTTP server and Zephyrus process cannot be assumed to survive disconnection; monitor their actual status during OTA and never begin a second transfer to compensate for an unverified first one.

## Code review and current operational boundary

Version v1.0.0 tests cover image/hash/URL mismatches, corrupt/active/unverified/historical campaign records, read-only audit, unique workdir enforcement, exact-target risk separation, serial qualification and a PM profile's separate check path. No live-device test or hardware claim is implied by hosted CI. The `cli7` bedroom socket's previously observed partial OTA stalls and parent path remain separate diagnostic risks; this workflow does not assert that the firmware can recover from an incomplete or failed boot.
