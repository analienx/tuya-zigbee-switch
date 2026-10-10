# BSEED OTA Resume Supervisor

Use `helper_scripts/bseed_ota_resume_supervisor.py` for a retry/resume of a
cross-role OTA and for **progress-gated same-role OTA resume** after a failed,
source-unchanged attempt. Do not hand-launch a long-running OTA child with
`stdout=PIPE`.

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

## Cross-role safe resume flow

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
   `allow_join_all_fallback` is explicitly enabled. Closure is attempted in
   `finally`, including after a failed open request. A close timeout or error
   leaves closure unconfirmed and blocks OTA. Check the saved close response
   and live bridge permit-join state before another attempt. Recovery succeeds
   only when a fresh read from the exact
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

## Same-role progress-gated auto-resume

`resume-same-role` exists for an exact same-role Router→Router or
Client→Client campaign. The primitive `bseed_ota_campaign.py --mode flash`
still performs exactly one firmware submission and never retries itself.

For every real flash attempt the supervisor reads only a transaction JSONL that
contains `ota_request_sent`, then records the maximum numeric
`device_state.update.progress`. OTA availability checks therefore cannot be
mistaken for transfer attempts. Automatic retry uses a strict monotonic gate:

- first failed flash: compare its maximum progress with a 0% baseline;
- later failed flash: require `current_max_progress > previous_max_progress`;
- equal, lower, missing or zero progress blocks another automatic flash;
- successful transport followed by interview/postflash failure also blocks
  reflashing, because transport success is already known.

Before every permitted retry, same-role mode uses a 15-second fast source
reconciliation that proves the exact source build/role, fresh target reachability
and no active OTA. Candidate availability is intentionally deferred. If the exact
target GET is missing, same-role mode uses `auto` recovery with **Join All
disabled**: verified scoped `join_via` first, coordinator-only second. Both
paths close their permit-join windows in `finally`. If reachability cannot be
restored, the result is `physical_intervention_required`, not another OTA.
After fast source verification the campaign enters a non-flashable
`source_verified_candidate_pending` phase; the old `LAST_CHECK` is archived and
the original shared network lock remains held. After PM source-build proof,
source-firmware telemetry quarantine is restored under the same network lock.
Fresh `preflight` must prove power from a newly decoded ZCL activePower sample,
not a cached composite power field. The exact `check` must then match fresh
transaction, IEEE, source URL, pinned image hash, and timestamp. Only then
is the campaign reconciled and the shared lock released for one new flash.
The updated Zigbee2MQTT converter is required for this fresh PM sample gate.
Cross-role resume keeps strict reconciliation. `OTA_SUPERVISOR.json` records
the progress history and current retry decision.

This allows retained-image protocol resume to continue when each iteration
makes objective forward progress, while preventing an unattended loop at the
same failing offset.

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

Cross-role resume/retry:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py resume-transition ^
  --profile C:\path\to\PRIVATE_profile.json ^
  --confirm-ieee 0xa4c13824a7005afb ^
  --confirm-load-unplugged ^
  --join-strategy auto ^
  --allow-join-all-fallback
```

Same-role progress-gated resume/retry:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py resume-same-role ^
  --profile C:\path\to\PRIVATE_profile.json ^
  --confirm-ieee 0xEXACT_TARGET_IEEE ^
  --max-attempts 12
```

For non-PM same-role firmware also supply `--confirm-load-unplugged`.
Same-role mode never enables Join All.

Read-only status:

```powershell
py -3 helper_scripts\bseed_ota_resume_supervisor.py status ^
  --profile C:\path\to\PRIVATE_profile.json
```

The status output includes the persisted supervisor record, whether its PID is
still alive, ACTIVE_LOCK/LIVE_STATUS, the newest OTA JSONL age, recent events,
and exact-IEEE/image activity diagnostics. Diagnosis selects the latest actual
transaction for this device/image, even when newer read-only check logs exist.
The activity report deliberately cannot infer real block-request inactivity
from percentage telemetry.
Its warning is intentional: stale observer output does not prove transport
failure.

## What the supervisor will not do

- It will not auto-kill an `ota_running` campaign.
- It will not infer OTA failure from a stale stdout/log/JSONL timestamp.
- It will not bypass the recovery gate, exact IEEE confirmation, load-unplugged
  confirmation, image SHA checks, role policy, or link
  qualification.
- It will not automatically retry a firmware transfer that actually returned a
  failed OTA result without first reconciling the exact source as unchanged.
  For same-role OTA it additionally requires strict forward progress versus the
  previous real flash attempt; equal/lower/missing progress is terminal for
  automatic retry.
- It will not release hardware acceptance or network ownership merely because
  a transport completed.

If an `ota_running` campaign appears wedged, first independently verify live
device/update state. Only after the transport is known to have stopped should
the canonical reconciliation path be used.


## 2026-10-10 hardening and efficient Hifi retry

- The same-role resume supervisor now obtains atomic, exclusive workdir ownership. A stale owner marker must be reviewed, not silently stolen. Network-wide OTA ownership is held through candidate verification.
- Every progress record must identify the exact target IEEE and image SHA-256. Progress from another campaign never unlocks another retry.
- PM idle proof requires the custom converter's raw activePower sample stamp and watts; the runner requests power afresh and rejects a repeated cached value. The converter must be deployed and its real reading verified before a live PM OTA.
- Fast reconciliation archives the previous check and enters a candidate-pending state. This state cannot flash. Check, finalization, and next flash are intentionally separate transitions.
- The first Hifi attempt reached 0.59 percent. The first supervised retry is progress-eligible against a zero baseline; each later failed iteration must improve strictly. The exact 32-byte/1,200-ms profile remains the conservative starting point, not a universal optimum.
- **Do not recommend 3 minutes for Hifi without block-request traces.** Five minutes previously failed for the non-PM Bedroom Client while it remained alive and a 30-minute wait later completed. Hifi's experimental 180,000 ms private profile change was reverted to 1,800,000 ms; 0.59% and 30 minutes without a published progress change do not establish true block silence. See docs/bseed_ota_timeout_evidence_policy_20261010.md. No OTA was sent.
- The primitive OTA runner submits only one transfer. No reset, power cycle, broad Join All, manual offset invention, or forced coordinator restart belongs in its normal path.
- Hifi remains blocked until GitHub-hosted CI is green at the exact final commit, the converter is deployed, and the fresh source, PM and candidate gates pass on live evidence.
