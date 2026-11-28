# BSEED OTA — relay-preserving, power-observing operator policy

Effective 23 November 2026, **automated BSEED OTA migration must never
intentionally switch a socket relay OFF**, even on a socket with a connected
appliance, and must not refuse an image based on wattage or unavailable PM
telemetry. This changes the runner's admission policy, **not** firmware
behavior during reboot.

## Mandatory behavior

1. Read a fresh target-specific relay baseline through Zigbee2MQTT:
   `state` for stock, `state_relay` for custom. Pin the observed `ON` or `OFF`
   into the private profile; never infer the relay from wattage.
2. Generate `relay_state_policy: preserve`,
   `auto_relay_off: false`, and
   `operator_connected_load_risk_accepted: true` for approved targets.
   Older profiles with `auto_relay_off: true` are invalid for OTA and must
   be replanned. The old direct OFF and rejoin-OFF helpers now reject calls.
3. Do not set the socket OFF at any preflight, flash, transition, join,
   reinterview, PM provision, or resume step. The only permitted relay
   mutation is a target-specific **ON-only restoration** if the baseline
   was ON and a reboot cleared it.
4. Read power/voltage/current for diagnostics where available. **There is
   no 1 W or 100 W OTA ceiling.** Missing, high, stale, anomalous or negative
   reported watts are not OTA admission gates. Record whether the reading
   was fresh; do not manufacture a 0 W reading or treat cached PM as fresh.
5. The exact device IEEE, sealed firmware hash, manufacturer/model, source
   and target role, successful interview, ZDO node descriptor where needed,
   Zigbee link, coordinator network leases and recovery policies remain
   strict. A connected-load authorization does not bypass them.
6. After firmware installation, verify the actual **new** build and live
   ZDO role before potentially sending ON to a socket that had been ON.
   If it was OFF before the update, never send ON or OFF automatically.
   Preserve original physical-continuity failures and negative acceptance
   evidence, even if targeted ON recovery succeeds.
7. An OTA firmware reboot can interrupt mains or reset the relay; **zero
   interruption cannot be guaranteed by scripting**. A socket supplying
   critical equipment must not be represented as continuity-safe merely
   because the relay is ON after recovery.

## Where implemented

- `helper_scripts/bseed_operator_load_policy.py` — common admission rule.
- `helper_scripts/bseed_socket_migrate.py` — live relay baseline, never
  creates auto-OFF profiles, stock-to-Client conversions remain exactly
  SHA/IEEE pinned and require existing explicit authorization.
- `helper_scripts/bseed_ota_campaign.py` and
  `helper_scripts/bseed_targeted_z2m_ota.py` — reject unsafe direct
  flash and do not gate on PM threshold or missing fresh watt reading.
- `helper_scripts/bseed_canary.py`, `bseed_ota_autopilot.py`, and
  `bseed_ota_resume_supervisor.py` — preserve-state for generated and
  resumed campaigns without forced unloading.
- `helper_scripts/bseed_nonpm_recovery_gate.py` — retains board-specific
  recovery proof but accepts owner-authorized connected-load OTA.
- `helper_scripts/bseed_relay_auto_off.py` — historical OFF functionality
  retired. Targeted ON restoration still possible.
- `helper_scripts/bseed_relay_postflash_restore.py` — custom image
  ON-only recovery after exact postflash ZDO/firmware identification.

## WifiRight incident — verified observations

The November 23 stock Router → cli27 OTA exhibited a 100% transfer reading
but stale Zigbee2MQTT metadata. A 254-second network-wide permit-join was
accepted at **12:17:44 Europe/Prague**. Later live inventory showed custom
`1.2.5-bseedcli27` and a successful interview, but cached type `Router`.
A **fresh live ZDO node descriptor confirmed logical type 2 / EndDevice**,
while a targeted relay GET confirmed **ON** and ~7 W reported at 12:25:52.
Do not mistake cached Z2M Router metadata or post-reboot ON for proof of
physical continuity during OTA.

## Tests and deployment

Run the focused policy suite on an authorized host:

```text
python -m pytest -q tests/test_bseed_relay_postflash_restore.py tests/test_bseed_relay_state_policy.py tests/test_bseed_relay_auto_off.py tests/test_bseed_socket_migrate.py tests/test_bseed_stock_client_loaded_pair.py tests/test_bseed_nonpm_ota_preflight.py tests/test_bseed_pm_physical_preflash.py tests/test_bseed_canary.py
```

Do not modify existing active campaign profiles or remove coordinator locks
to apply this change. New scripts take effect after deployment on the machine.
A GitHub commit/PR is source publication, **not** an OTA or runtime deployment.
