# Issue 55 service incident and completion gates — 2026-09-29

Live diagnostics separated the broker from the Zigbee service. EMQX was running
and healthy. Zigbee2MQTT 2.14.0 / Herdsman 10.9.1 was in error state.

At 17:47–17:48 Europe/Prague, the bounded non-PM source recovery received
`SRSP - ZDO - mgmtPermitJoinReq after 6000ms` on open and
`SRSP - AF - dataRequestExt after 6000ms` on close. Later logs show coordinator
timeouts across multiple devices. These are network-wide adapter failures;
they do not establish a defect in the Bedroom canary firmware.

A bridge restart at 17:57 failed to disable joining and obtain the coordinator
version, then failed to lock the serial port. A subsequent start at 18:04 opened
the port but failed with `SRSP - SYS - ping after 6000ms`. One bounded restart
through the canonical `restart_known_ha_app.py` helper reproduced that ping
failure at 18:09. The coordinator still enumerated over USB and no process held
the Zigbee serial port after exit. The underlying cause of the adapter becoming
unresponsive is not established. No coordinator firmware, network identity,
database, radio settings or fleet OTA index was changed in this investigation.

The resume supervisor previously reported that permit-join "was closed" for
any nonzero recovery exit. That claim was incorrect when the close itself
failed. It now reports unconfirmed closure, points to the private evidence and
requires checking live bridge state. Regression coverage requires preserving
the campaign lock and refusing qualification/OTA launch after a close failure.

PR #56 is the software remediation. Its merge does not close hardware acceptance
or authorize a fleet release. Keep issue #55 open until the outstanding gates
are evidenced:

1. Restore the coordinator/service and positively verify joining is closed.
2. Reconcile the original non-PM campaign with fresh exact source build/role,
   target IEEE, OTA quietness and candidate identity; preserve unresolved locks.
3. With current physical-load confirmation, freshly qualify and resume
   through the deterministic supervisor, retaining durable transaction evidence.
4. Verify the exact non-PM Router build and live role, relay/settings retention,
   absence of PM reporting and actual child-parenting/routing behavior.
5. Complete the exact reverse Client transition and its acceptance gates.
6. Finish longer soak, broader mesh/parent-loss evidence and the separate
   fixture-driven PM release gate.

The Kitchen PM A/B/A canary passed the role-pair checks recorded in
`bseed_force_hardware_acceptance_20260928.md`. The Bedroom non-PM round trip has
not passed. All repository validation must run on GitHub-hosted Actions at the
exact candidate SHA. Household profiles, credentials and runtime evidence remain
private; this record contains only the sanitized incident summary.
