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
3. If the prior campaign phase is `update_error` or
   `update_timeout_or_unconfirmed`, run canonical `reconcile-source`.
4. Require the campaign to be either new or
   `source_unchanged_reconciled`.
5. Run canonical `qualify` synchronously, logging directly to disk.
6. Immediately launch canonical `transition` so link-gate evidence cannot age
   between qualification and OTA launch.
7. Redirect child stdout and stderr to a durable transition log; stdin is
   `DEVNULL`; no stdout/stderr PIPE is created.
8. Persist `OTA_SUPERVISOR.json` with PID, exact profile SHA, image SHA, log
   paths, launch contract and timestamps.
9. Return immediately. Later checks use `status`.

For the exact Bedroom non-PM canary, profile loading separately enforces the
known-good transfer envelope: 32-byte blocks, >=1200 ms response delay,
>=1,800,000 ms per-request timeout and >=14,400 s overall monitor.

## Commands

Resume/retry:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py resume-transition ^
  --profile C:\path\to\PRIVATE_profile.json ^
  --confirm-ieee 0xa4c13824a7005afb ^
  --confirm-load-unplugged ^
  --accept-nonrecoverable-ota-risk
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
