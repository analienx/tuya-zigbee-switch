# Issue 55 remediation finalization

This record supersedes the pending-source statements in the original triage and
two patch drafts. The candidate is published in PR #56. CI results are specific
to each candidate SHA; consult that PR's current checks and the candidate
finalization artifact. No local execution counts as verification.

Current completion boundaries are in `docs/bseed_issue55_resume_20260929.md`.
The four current native socket candidates and exact-source sealed hashes are
recorded in `docs/pr56_second_review_remediation.md`; the six-tuple report below
describes the historical first finalization. The Kitchen PM role-pair canary
passed A/B/A checks; non-PM and broader release acceptance remain open.

## Source disposition

All twelve original findings and the seven wider-review findings have source
changes and regression coverage. The original triage table is historical, not
the current implementation state. Non-PM matrix wiring, the other legacy CI
workflow, mandatory board-derived PM mode, fresh telemetry-release evidence,
and the separately constrained TS0726 policy are implemented.

The finalization pass found and corrected defects in the draft remediation:

- Parse the ZCL command at header byte 2 (or manufacturer-specific byte 4),
  enforce both application and storage string capacities before SDK dispatch,
  and complete configuration harness dependencies.
- Seed energy deltas when the persisted baseline becomes available, preserving
  subsequent accumulation while never replacing an unknown baseline with zero.
- Preserve network ownership after a software `postflash_candidate`. Hardware
  acceptance is not inferred from a verifier subprocess returning zero.
- Pin network ownership to the actual coordinator IEEE, independently of campaign
  work directories. Every runner host must use the same atomic shared directory.
- Provide a concrete read-only Z2M backend for the non-PM link gate, using an
  awaited Herdsman read with a selected transaction sequence and endpoint 2.
- Replace the TS0726 canary's overlength Basic name with a fresh short identity
  `1.1.9-bseedlv2` / `0x1102300E`; historical identities and stored bytes remain unchanged.
- Retire stub scheduler events before invoking callbacks so a task that
  reschedules itself stays pending. Bound relay-test loops and exercise reset
  retries through the scheduler. Tests also enforce protection-owned rearming.

## Public CI and packaging

The six socket candidate identities are sealed in the registry to bytes built
from `9bd93fc3157041bd2c1dcab34af7a6d75ff8864c`. The exact-source run passed
935 tests, formatting, converter drift checks and all native build gates.
The [Actions finalization report](https://github.com/analienx/tuya-zigbee-switch/actions/runs/36336421938)
records the verified hashes and prerequisite runs. This is candidate sealing;
registry status/notes do not grant hardware or fleet release acceptance.

All builds, tests, formatting, verification and sealing run on GitHub-hosted
Actions. `BSEED candidate finalization` waits for successful exact-head test,
PM matrix, non-PM matrix, Router and experimental Client workflows. It downloads
their artifacts on the runner and runs the combined six-tuple verifier/sealer
against a copy of the registry. Its artifact contains a sealed registry proposal
and `CI_SEAL_REPORT.json`, with the source SHA, hashes and prerequisite run URLs.
It does not commit the registry, change an OTA index, release firmware or flash.
Reviewing/committing a proposal is a distinct publication step; modified source
must receive its own green CI. Never execute the sealer on a personal checkout.

## Non-PM downlink backend contract

The optional `zigbee2mqtt/extensions/bseed_link_probe.js` targets Z2M 2.14.0 and
Herdsman 10.9.1. Installation into the live service is separately authorized;
this source change does not install it. The bundled `bseed_link_probe.py` is the
default link-gate client and uses the private campaign MQTT settings. A private
`link_probe_command` override may implement the same evidence contract.

The backend only permits one bounded read of endpoint-2 `genOnOff.onOff` on the
exact custom non-PM board/name/IEEE. It never changes relay state, joins devices,
interviews, retries or sends OTA. A random transaction sequence and request ID
bind each result; expired/repeated requests, missing values and read timeouts
fail. Hosts need synchronized clocks; disagreement fails closed. Unsolicited
MQTT state cannot substitute for the awaited read. Missing backend installation
therefore blocks qualification rather than silently weakening it.

## Private network ownership

Profiles require `network_lock_dir`, `network_lock_shared: true`, and `network_id`
equal to the live coordinator IEEE. The directory must be outside the repository
and independent of the campaign workdir, shared by every runner host. Migration
from old workdir-only campaigns requires reconciling any existing unresolved
campaign before using this authority. A clean new directory is not permission
to ignore previous live-device evidence. Failed transfers and software-only
postflash candidates retain ownership. Explicit hardware acceptance/reconciliation
must inspect the actual device before an operator releases that ownership.

## Still separate from software completion

Neither green CI nor a sealed proposal proves boot or hardware acceptance.
Device-specific authorization and acceptance still cover fresh installed build
and role, commands after idle, parent/rejoin behavior, actual relay output,
meter scaling and loaded-to-zero response, settings/energy retention, Router
parenting, and soak. Initial upgrades from firmware without the checkpoint and
sudden power loss retain their separately documented energy limitations.

The original review and remediation issue remains open for these acceptance
requirements; no fleet release is implied by PR #56.

The former version-bumped PM Client-to-Router return package is retired from
current build, seal and CI paths. Its `.17` tuples remain registry tombstones only
to prevent identity reuse. Equal-version Router/Client canary interchange uses the
private FORCE test transport around the exact sealed native candidates; that test
transport is not a fleet release and does not establish boot, radio or hardware
acceptance by itself.
