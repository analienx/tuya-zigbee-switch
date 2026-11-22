# Same-role OTA autopilot

`helper_scripts/bseed_ota_autopilot.py` takes one planned same-role campaign
(a `profile.json` from `bseed_socket_migrate.py plan` or `bseed_canary.py`) to
`postflash_candidate` without hand-built retries.

```bash
python helper_scripts/bseed_ota_autopilot.py run --profile <workdir>/profile.json --confirm-ieee 0xEXACT
python helper_scripts/bseed_ota_autopilot.py status --profile <workdir>/profile.json
```

## Parallel OTA: canonical scheduling and independent failure recovery

`helper_scripts/bseed_ota_autopilot.py` is the supported **same-role**
concurrent orchestrator, rather than a serial `bseed_socket_migrate.py run`
queue. The current limit is **five per-IEEE coordinator slots**. Each device
runs its own supervised campaign with an independent OTA token, sealed image,
work lock, progress history, retry budget and source reconciliation.
**A failure on one target does not cancel healthy peers.**

- **Fresh group:** `run-many` launches distinct profiles 20 seconds apart;
  every profile must pin the exact target IEEE, set `parallel_ota_max` at
  least to group size and include all other group members in
  `concurrent_ota_ieees`. It waits for all children and reports each result.
- **Admit a peer while another runs:** start only the **new** profile with
  `run --profile ... --confirm-ieee ... --site ...`. Its allowlist must cover
  *all* existing slot owners, including completed but unreleased candidates.
  Existing owners' exact work locks are verified via the lease's
  `work_lock_path`, or supplied in `peer_work_locks` for older lease records.
  A freshly updating peer must have recent, transaction-matched progress.
  Do not re-launch `run-many` against a campaign already flashing, nor
  mutate someone else's active lease to simulate a free slot.
- **Failure recovery:** an ABORT/timeout is reconciled for that IEEE alone.
  If the original build/role remains installed and the OTA is inactive, the
  autopilot rechecks the image and preflight and retries only while the
  progress gate advances. A refusal before bytes are sent does **not**
  consume a flash attempt; the autopilot waits for admission with a bounded
  peer budget. Persistent failures stop only that worker, not the group.
- **Completed is not released:** `postflash_candidate` is software install
  evidence, not automatic hardware acceptance or a free slot. Preserve
  evidence; release or quarantine a lease only via the canonical evidence-
  backed owner/broker flow. Do not delete JSON leases or fabricate acceptance.
- **Rates and recovery:** minimum response pacing 1800 ms for three slots,
  2400 ms for four/five, 32/48-byte blocks, exact sealed image SHA and
  per-device load/relay verification. Same-role only, no FORCE, no Join All,
  `source_rejoin_strategy: none`, and independent source recovery.
- **Dynamic topology:** a Router-to-child dependency must be checked using
  a *fresh* exact-IEEE neighbor/route snapshot, not carried forward from
  an earlier run. Avoid concurrent updating of a Router and its **currently
  confirmed** child. A missing entry alone is not definitive proof of an
  alternative uplink: treat uncertain routes conservatively. There is no
  unconditional scheduler ban on two unrelated, healthy Router/Client OTAs.
- **Relay policy:** maintain the actual ON/OFF state of each appliance,
  `auto_relay_off: false`, `relay_state_policy: preserve`. PM preflight
  generally requires a fresh device-originated power sample; any prior
  explicitly approved exception must remain device-specific and auditable.

### Adding one peer to a live multi-slot group

Prepare/publish the exact new profile first. Ensure all existing owners
are allowlisted and each has a valid active or pending lease.

```bash
python helper_scripts/bseed_ota_autopilot.py run   --profile /private/new-target/profile.json --confirm-ieee 0xEXACTIEEE   --site /private/site.json --max-attempts 12
```

Use `run-many` when starting a group from scratch. Use single `run` to join
an existing group; it still obtains its own coordinator slot and supervises
all retries. Do not start a second worker on the same IEEE.

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

Pick group members with strong routes (see below). Avoid pairing a Router with a **currently confirmed** child; check live topology, not a historical neighbor snapshot.

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
- Avoid simultaneously flashing a Router and its **currently confirmed** child. Check live parent/neighbor evidence; an old relationship is not proof of the current route.


### Recovery: stock Router to custom Router after 100 percent transfer

The stock Router to custom Router canary retains the Router Zigbee role but
may need a fresh network association due to the firmware change. A narrowly
pinned, no-reflash network-wide rejoin now requires --stock-router-profile
pointing to the profile.json in the same directory as the completed campaign
ACTIVE_LOCK.json. Supply --network-wide --from-role Router --expect-role Router,
target exact name/IEEE twice, expected BSEED r24 build, and a bounded
--seconds 120 --total-seconds 300. The tool checks the successful stock OTA
transaction (version 77 -> 0xffffffff), hash, model, role and preserved
OFF relay, and refuses ordinary same-role updates, rejected OTAs and
missing identities. Foreign joiners close the network immediately.
Rejoining is only a candidate; a fresh target interview, identity, radio,
relay and PM validation are required separately. Never automatically reflash
a postflash-unverified target.
