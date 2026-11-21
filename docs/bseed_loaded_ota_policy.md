# BSEED OTA — permanent three-slot preserve-relay policy

Operator-approved on 2026-11-21. This is a repository policy, not a one-day campaign setting.

## Concurrency

- **Maximum 3 simultaneous Zigbee OTA transfers per coordinator.** No fourth admission while three existing transfers are active.
- New preserve-state `bseed_canary` campaign profiles default to `parallel_ota_max=3`. An explicit lower setting is allowed. Legacy singleton and two-slot campaigns already running must remain unchanged.
- Every parallel admission uses the coordinator's shared atomic filesystem lease. Three-slot admission verifies an exact list of *all currently active* peer IEEE addresses (0–2), unchanged local work locks, transaction tokens and firmware SHA256 values, and fresh progress evidence from each peer.
- The third slot may join an already running legacy singleton plus a two-slot campaign only when all live lease identities and progress match. No active lease is rewritten or released to create a slot.
- Same-role firmware only; no FORCE role conversion, permit-join-all, or source rejoin automation in parallel mode. OTA blocks must be 32 or 48 bytes, with at least 1800 ms configured pacing per transfer.
- The limit is a concurrency **ceiling**, not a requirement to start three updates. Other blockers (offline device, mismatched IEEE/source firmware, stale or unresolved OTA, broken coordinator authority) still refuse admission.
- Dual-slot legacy profiles remain supported with their original reciprocal-peer rules. Other single-slot campaigns remain supported.

## Relay and power

- Preserve the socket's existing state. **Never issue ON or OFF commands solely to flash firmware.** Read `state_relay` immediately before the flash and match the pinned ON/OFF state.
- A power-meter preflight remains the usual PM policy. The explicit `operator_waive_preflash_pm=true` exception is currently restricted to the three owner-approved same-role PM sockets: `LivingRoomSocketWifiLeft`, `KitchenSocketLeft`, and `KitchenSocketRight`, each with its exact IEEE. It skips only *fresh preflash power proof*. It records `operator_waived_unknown_power` rather than inventing a 0 W reading.
- The waiver **does not** skip firmware seal validation, live source/role check, OTA availability check, relay-state read, coordinator ownership or postflash acceptance checks.
- **Firmware activation can still interrupt output power**, because the MCU may reboot or change the hardware relay. The updater does not guarantee zero interruption.
- Previous postflash retention failures remain negative evidence and are never silently reclassified as accepted. A new candidate is separate, with its own authorization and workdir.

## Required postflash evidence

1. OTA response success is a transfer result, not hardware acceptance.
2. Reinterview and independently confirm installed firmware, role, and relay status.
3. Verify preserved relay state and power-meter reporting; investigate unexpected state or configuration drift.
4. Restore documented group membership where applicable, without toggling the load.
5. Release or terminalize each coordinator lease only after the matching evidence-backed transition, without deleting prior failures.
