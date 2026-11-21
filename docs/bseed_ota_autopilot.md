# Same-role OTA autopilot

`helper_scripts/bseed_ota_autopilot.py` takes one planned same-role campaign
(a `profile.json` from `bseed_socket_migrate.py plan` or `bseed_canary.py`) to
`postflash_candidate` without hand-built retries.

```bash
python helper_scripts/bseed_ota_autopilot.py run --profile <workdir>/profile.json --confirm-ieee 0xEXACT
python helper_scripts/bseed_ota_autopilot.py status --profile <workdir>/profile.json
```

## Up to five sockets at once

```bash
python helper_scripts/bseed_ota_autopilot.py run-many \
  --profile A/profile.json --confirm-ieee 0xA... \
  --profile B/profile.json --confirm-ieee 0xB... \
  --profile C/profile.json --confirm-ieee 0xC...
```

`run-many` starts one autopilot per socket, 20 s apart (`--stagger-seconds`).
Each takes its own coordinator slot. It then waits for all of them and prints
each socket's final state, attempt count and best progress. It refuses the
group unless every profile:

- has `parallel_ota_max` of at least the group size (2–5);
- lists every other member in `concurrent_ota_ieees`;
- uses `source_rejoin_strategy: none`.

Slot rules (`bseed_network_campaign_lock.py`):

- **Allowlist, not exact set.** A campaign may start while every running or
  slot-holding OTA is on its `concurrent_ota_ieees` allowlist. The profile's
  peer list may name peers that are not running yet, so one fixed profile
  serves every resume. (Until now the list had to equal the running set, so
  each retry needed a new hand-made profile.)
- **Between attempts.** A peer that is between attempts still holds its slot
  and must have its own work lock. A running peer must be freshly updating.
- **Capacity** is the smallest `parallel_ota_max` of any participant. A legacy
  or two-slot owner allows three in total, as before.
- **Pacing.** 3 slots need at least 1800 ms response pacing per socket; 4–5
  slots need at least 2400 ms. That keeps the coordinator's total load no
  higher than three transfers at 1800 ms.

Pick group members with strong routes (see below), and never put a Router in
the same group as one of its own children.

## Relay read-back

The preflight's read-only relay GET is re-sent up to 3 times. If the socket
never answers it, the update continues and logs `relay_get_unverified`. The
PM load proof and the automatic relay-OFF step stand on their own (owner rule,
2026-11-21). A relay that answers in the **wrong** state still refuses the
update.

Pass `--site <private site.json>` (also to `run-many`) so the autopilot can
re-publish an image that is no longer served. It does that before any attempt
whose image URL returns an error or a different hash, uploading the profile's
own hash-checked image and index. Without `--site` it stops with
`image_not_served`. Progress is read from `device_state` and also from
Zigbee2MQTT's own `OTA update of '0x…' at N%` log lines.

## What it does

The autopilot repeats until the socket is installed or a stop rule applies:

1. **Reconcile.** An interrupted transfer (`update_error` / timeout) goes through
   the fast source reconciliation.
   - In multi-slot mode the profile must have `source_rejoin_strategy: none`,
     and the reconciliation does no live rejoin or permit-join.
   - Otherwise it uses the profile's own strategy.
2. **Preflight and check.** Each is retried up to `--step-retries` times,
   `--step-retry-seconds` apart. A weak route can drop one read-only GET; the
   campaign's relay GET also re-sends itself up to 3 times.
3. **Flash.** The campaign's own `flash` runs.
   - The attempt's first and last progress, start, end, Zigbee2MQTT error and
     final phase are appended to `OTA_AUTOPILOT.json`.
   - An attempt that stopped before sending anything (`preflight_abort`) is
     simply started again.
4. **Post-flash.** A finished transfer whose post-flash check came back
   unconfirmed (`ota_transfer_ok_postflash_unverified`) gets up to
   `--postflash-retries` re-runs of `postflash`.

A flash that sends nothing is **not an attempt**. That covers being refused
at slot admission (for example while a peer is between attempts) and being
stopped by its own pre-checks. The autopilot records it under `refusals`,
waits `--peer-wait-seconds` (60), and tries again. After
`--peer-wait-budget-seconds` (1800) of such waiting it stops with
`peer_wait_timeout`. (The first live pair on 2026-11-21 burned all 12
attempts on KitchenSocketLeft in two minutes this way.)

To stop a run cleanly, use `bseed_ota_autopilot.py stop --profile P`. It
writes `AUTOPILOT_STOP` in the workdir. The attempt in progress finishes, and
the autopilot then exits with `stopped_by_operator`; a new run refuses to
start until the file is removed.

Stop rules:

- `postflash_candidate` → `installed`. Releasing the lease still needs the
  owner's words (`migrate release`).
- More than `--max-stagnant` (default 2) consecutive attempts that do not beat
  the best progress so far by more than `--min-gain` (0.5 percentage points)
  → `progress_stalled`. One cheap dud, for example a
  lost GET, does not end the run.
- `--max-attempts` (default 12) → `attempt_budget_exhausted`.
- `ota_running` owned by another process, a non-recoverable phase, or a failed
  reconciliation or preflight → stops with the reason recorded. It never
  forces anything.

It never changes roles, never edits the profile and never releases a lease.
It uses the resume supervisor's per-workdir owner lock, so two processes cannot
drive the same campaign.

## Why transfers need resumes, and the settings that matter

KitchenSocketLeft and HifiLeft (2026-11-21, cli25 → cli27) needed 11 and 7
attempts:

- Each attempt moved data for 2–12 minutes. Then the socket's Telink OTA client
  gave up: it waits 5 s per block response and gives up after 10 retries, so
  about 50 s of lost responses ends a download.
- A mains Client gets every block through its parent Router. Kitchen's own link
  was strong (LQI 222), but its parent's uplink was LQI 70. One weak hop on the
  route is enough.
- Progress is kept, so every attempt resumes from its offset. The time went
  elsewhere:
  - each abort idled `request_timeout_ms` (900 s in those profiles) before
    Zigbee2MQTT failed it;
  - the next attempt was then rebuilt by hand 40–60 minutes later.

What to do:

- Keep `request_timeout_ms` at the 300000 canary default
  ([timeout policy](bseed_ota_timeout_evidence_policy_20261010.md)). The
  autopilot reports a longer value as an advisory. Bedroom FORCE keeps its
  1800000 minimum.
- Pair sockets in multi-slot mode only when both routes are strong. Check the
  parent's own uplink (`zigbee_uplink_lqi` on the parent), not only the
  socket's. Run weak-route sockets alone, which also allows 1200 ms pacing.
- Never flash a Router while one of its own children is flashing.
