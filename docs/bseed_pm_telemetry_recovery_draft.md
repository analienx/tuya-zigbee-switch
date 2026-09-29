# PM telemetry recovery follow-up — APPLIED as finding C remediation

Current source status: the draft fixes are applied in PR #56. See
[`docs/bseed_issue55_finalization.md`](https://github.com/analienx/tuya-zigbee-switch/blob/codex/issue-55-finalize-remediation/docs/bseed_issue55_finalization.md)
for the superseding implementation and GitHub-hosted-only validation path.
Pending-source/approval statements below are historical. Run no repository
build, test, lint, verification or sealing on a local machine.

Status: the bounded fix below was applied to
`helper_scripts/bseed_z2m_metadata_refresh.py`,
`helper_scripts/bseed_pm_telemetry_guard.py`,
`helper_scripts/bseed_ota_campaign.py` and
`helper_scripts/bseed_z2m_rejoin_window.py`, with regression tests in
`tests/test_bseed_pm_telemetry_guard.py` and
`tests/test_bseed_ota_campaign.py`. This draft is retained as the review
record; the helper sources are authoritative. No finding is closed: exact-SHA
public CI, follow-up independent review and hardware gates remain open.

The current candidate suppresses PM measurements during an OTA identity change.
Its guard has matching campaign/IEEE/hash/build/role checks, but the cross-role
orchestrator supplies the rejoin inventory captured before metadata correction.
That inventory may still say Router after a verified Client boot, so release is
refused even after metadata was corrected. The standalone metadata resume mode
currently does not attempt guard release at all.

Automatic approval review had blocked changing this live recovery
orchestration. The bounded fix prepared here for review has since been applied
in source (see status above); no Home Assistant, MQTT or device contact
occurred as part of that source change.

## Proposed change

1. Extend `bseed_z2m_metadata_refresh.py` evidence with the final exact target
   inventory and final live ZDO descriptor. For quarantine recovery, require a
   transaction-matched successful target interview and fresh nonretained
   inventory observed after requesting it. A cached build plus fresh ZDO alone
   must not be labeled a fresh Basic identity check. Bound the interview to one
   attempt per invocation and preserve evidence on failure.
2. Extend `bseed_pm_telemetry_guard.validate_release` to accept this verified
   metadata evidence, requiring completed interview, fresh identity, expected
   IEEE/build/role, final live role and no error. Retain the guard's matching
   enabled phase, image hash, token, successful-transfer lock and timestamp
   checks. Do not accept merely `metadata_already_correct` without fresh proof.
3. After successful cross-role metadata correction, release using that final
   metadata evidence, replacing the older rejoin inventory as release input.
   A separately invoked, successful metadata resume can release the matching
   guard too. Failure leaves it active and does not submit another OTA.
4. Do not remove or silently adopt a guard after a lost enable ACK, a missing
   campaign lock or an already-active quarantine. These uncertain cases need
   separate bounded reconciliation. They must not fall into the normal success
   path merely to restore measurements.

## Required evidence

CI tests should cover stale rejoin type followed by fresh correct metadata;
metadata-only resume; retained/stale inventory; failed or foreign transaction;
wrong build/role/IEEE; missing or mismatched guard/lock; lost option ACK; and
an already-correct cached row without a fresh interview. The valid path must
disable only this target's option and archive the matched private guard. No
test may contact a live broker.

Lifecycle regression tests already added to `test_bseed_pm_telemetry_guard.py`
cover the current begin/release implementation with a fake bridge, including
uncertain acknowledgements and identity changes. They have not run in CI and
do not prove this proposed recovery fix, which remains unapplied.
