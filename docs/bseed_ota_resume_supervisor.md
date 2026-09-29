# BSEED OTA Resume Supervisor

Use `helper_scripts/bseed_ota_resume_supervisor.py` for a retry/resume of a
cross-role OTA after a failed or source-unchanged attempt. Do not hand-launch a
long-running OTA child with `stdout=PIPE`.

## Why this exists

Python documents that a child can deadlock when stdout/stderr are pipes and the
parent does not continuously drain them. Long-running OTA emits enough progress
and MQTT/log output to hit that failure mode. For unattended OTA, redirect
stdout/stderr directly to a durable file (or actively drain with a dedicated
reader); this supervisor uses durable files.

Zigbee2MQTT OTA is a separate engine from this observer process. A stale local
JSONL/log or blocked observer therefore is **not proof** that the actual OTA
transport stopped. Never kill/reconcile an `ota_running` campaign solely
because observer output stopped.

## Safe resume flow

The supervisor performs this sequence:

1. Load and validate the private campaign profile.
2. Refuse a duplicate supervised child if the previous supervisor PID is alive.
3. If the prior campaign is `update_error`, `update_timeout_or_unconfirmed`,
   or an orphaned `ota_running` state whose previous supervised process is no
   longer alive, attempt canonical `reconcile-source`.
4. If and only if that latest reconciliation failure is exactly `Fresh target
   GET response missing`, run `bseed_source_rejoin_recovery.py` with the resolved
   recovery policy: `none`, `scoped`, `coordinator`, `all`, or `auto`.
   `scoped` uses the profile's verified `join_via` router; `coordinator` opens
   only the coordinator; `all` opens a bounded network-wide permit-join window;
   `auto` prefers scoped, then coordinator, and may fall back to Join All when
   `allow_join_all_fallback` is explicitly enabled. Every opened window is
   closed in `finally`. Recovery succeeds only when a fresh read from the exact
   target IEEE is observed; unrelated joins never count. Identity/hash/lock/
   candidate failures never open any join window.
5. After link recovery, rerun canonical `reconcile-source`; it remains the
   authority for exact source role/build, quiet OTA state, candidate source and
   network-lock release. Timeout or failed proof is a hard stop and never
   triggers an OTA launch.
6. Run canonical `qualify` synchronously, logging directly to disk.
7. Immediately launch canonical `transition` so link-gate evidence cannot age
   between qualification and OTA launch.
8. Redirect child stdout and stderr to a durable transition log; stdin is
   `DEVNULL`; no stdout/stderr PIPE is created.
9. Persist `OTA_SUPERVISOR.json` with PID, exact profile SHA, image SHA, log
   paths, launch contract and timestamps.
10. Return immediately. Later checks use `status`.

For the exact Bedroom non-PM canary, profile loading separately enforces the
known-good transfer envelope: 32-byte blocks, >=1200 ms response delay,
>=1,800,000 ms per-request timeout and >=14,400 s overall monitor.

## Rejoin policy

Profiles may declare `source_rejoin_strategy` as `none`, `scoped`,
`coordinator`, `all`, or `auto`. `auto` is the default and tries the narrowest
available route first: verified scoped router, then coordinator. `allow_join_all_fallback`
defaults to false and controls only the broadest network-wide fallback.
CLI flags override the profile for one run. `all` is intentionally supported as
a reusable recovery mechanism; its broader join window does not weaken target
acceptance because success still requires a fresh response carrying the exact
campaign IEEE, followed by canonical source reconciliation.

Recommended defaults:

- stable local router known: `auto`, fallback false;
- no trusted router but coordinator path is sufficient: `coordinator`;
- topology uncertain but bounded Join All is acceptable: `auto`, fallback true;
- explicitly force network-wide join for recovery: `all`;
- recovery must never open permit-join: `none`.

The resolved policy is persisted in `OTA_SUPERVISOR.json`.

## Commands

Resume/retry:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py resume-transition ^
  --profile C:\path\to\PRIVATE_profile.json ^
  --confirm-ieee 0xa4c13824a7005afb ^
  --confirm-load-unplugged ^
  --accept-nonrecoverable-ota-risk ^
  --join-strategy auto ^
  --allow-join-all-fallback
```

Read-only status:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py status ^
  --profile C:\path\to\PRIVATE_profile.json
```

The status output includes the persisted supervisor record, whether its PID is
still alive, ACTIVE_LOCK/LIVE_STATUS, the newest OTA JSONL age and recent events.
Its warning is intentional: stale observer output does not prove transport
failure.

## What the supervisor will not do

- It will not auto-kill an `ota_running` campaign.
- It will not infer OTA failure from a stale stdout/log/JSONL timestamp.
- It will not bypass the recovery gate, exact IEEE confirmation, load-unplugged
  confirmation, risk acknowledgement, image SHA checks, role policy, or link
  qualification.
- It will not automatically retry a firmware transfer that actually returned a
  failed OTA result without first reconciling the exact source as unchanged.
- It will not release hardware acceptance or network ownership merely because
  a transport completed.

If an `ota_running` campaign appears wedged, first independently verify live
device/update state. Only after the transport is known to have stopped should
the canonical reconciliation path be used.
