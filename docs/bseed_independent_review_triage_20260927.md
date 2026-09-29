# Independent review triage — 2026-09-27

Current source status: the draft fixes are applied in PR #56. See
[`docs/bseed_issue55_finalization.md`](https://github.com/analienx/tuya-zigbee-switch/blob/codex/issue-55-finalize-remediation/docs/bseed_issue55_finalization.md)
for the superseding implementation and GitHub-hosted-only validation path.
Pending-source/approval statements below are historical. Run no repository
build, test, lint, verification or sealing on a local machine.

Source: [issue 55 independent review](https://github.com/analienx/tuya-zigbee-switch/issues/55#issuecomment-5853890662).
Reviewer snapshot: `9fa1479338ce599d5d8f35b8398b845937ecb7b36185e56856859ab7e81e288b`,
based on `cd46953dd37210bf0b01f61977e92be2a0e39a57` plus the uncommitted candidate.

Decision: **hold candidate release and hardware progression.** The independent
review is RED for all four variants. The working source paths substantiate the
main findings; there is no basis for overruling the result. This triage records
assessment, not completed remediation or fresh test results.

## Findings and disposition

| Review finding | Assessment and required response |
| --- | --- |
| 1. Non-PM board guard omitted by native release scripts | Confirmed: both Client cases use empty extra arguments and the Router arguments omit the guard. Restore it in actual release paths and test compiler inputs, not just guarded host fixtures. |
| 2. PM energy lost at controlled OTA reboot | Confirmed: ordinary checkpoints are five minutes apart; OTA success directly reboots. Introduce a controlled-reboot checkpoint with explicit NVM failure handling. A blind write followed by reboot would still leave retention unproven. Test both roles, accumulation before the next periodic checkpoint, reboot restoration and write failure. Sudden power-loss retention is a separate limitation. |
| 3. Non-PM Router-to-Client tooling rejects its advertised transition | Confirmed composition mismatch: the recovery contract only accepts EndDevice-to-EndDevice and the outer transition rejects/does not propagate the physical confirmation. Implement a separately reviewed cross-role contract, or explicitly mark the path unsupported. Do not relax the existing same-role gate or transfer a historical device/image-specific exception to new hardware or builds. |
| 4. Optimized Python bypasses link evidence validation | Confirmed assertion-based validation remains. Replace runtime gate assertions with explicit exceptions and test optimized execution. The main OTA runner's existing optimized-mode refusal remains useful; this finding does not prove every complete flash path bypasses all controls. |
| 5. Required workflows rebuild historical sealed identities | Confirmed: the Router workflow does not select consolidated PM, and non-PM CI still compares historical control bytes. Make current-source candidate jobs use fresh identities; keep historical-byte verification separate. Do not weaken immutable hash checks to obtain green CI. |
| 6. Non-PM date remains wall-clock-dependent | Confirmed: non-PM paths omit the explicit release date. Pin dates and allocate new identities for changed source; preserve sealed releases. |
| 7. Documented runtime version/role policy absent | Current campaign has no `require_increasing` policy and the skill still refers to it. Derive policy from authoritative release/registry data and validate each mutation boundary. In particular, r7 and cli11 share a numeric version and are not a valid equal-version role-change pair. No claim that default Herdsman automatically allows downgrades. |
| 8. Stale build metadata permits wrong-scale early reports | Confirmed logic gap: fixed scales require a recognized cached build, so new raw data with an old/unknown build follows the legacy converter. Define fail-closed telemetry behavior during a known transition until fresh identity is established; do not apply new scaling to every legacy device. This differs from cli6-to-cli11's nominal known-build path. |
| 9. Textual converter test selects helper literal | Confirmed: first-occurrence model lookup now precedes the definitions array. Scope parsing to definitions or rely on real prepared definitions while preserving the tested exposure contract. |
| 10. Other build/evidence assertions disappear under optimization | Confirmed in inline wrapper validators and postflash verifier. Audit and replace critical assertions across the complete tool chain, with negative tests. |
| 11. Checked-in converter differs from intended generated artifact | Confirmed: template changes have no corresponding checked-in converter update. Select one canonical release path and enforce drift/hash checks so an executor cannot inadvertently deploy the old file. |
| 12. Emergency fallback bypasses exact board validator | Treat as explicit hardening. The minimal fallback has no relay mapping, so this is not established as unsafe pin activation. Specify and test a board-safe minimal state or safe initialization failure rather than introducing reboot loops or erasing recovery data. |

## What remains supported

The review supports the mains Client's receiver-on/no-sleep configuration,
separation from Poll Control, application polling state machine, native SDK PM
attribute registration and known-build converter reporting/scaling. It also
supports the short Basic-name implementation and Router keepalive capability
advertisement. These source/host observations do not establish radio reliability,
physical meter accuracy, boot/application success or fleet readiness.

The reviewer reported local counterexamples and targeted host tests; they were
not rerun during this triage and are not exact-SHA public CI evidence. No TC32
candidate build or hardware GREEN was provided. In particular, uplink surviving
while downlink fails remains an open hardware acceptance question.

## Remediation order and exit criteria

1. Repair actual release flags and controlled-reboot energy persistence.
2. Make all four native build/CI paths candidate-aware, with explicit dates,
   unique valid identities and reproducibility; preserve historical hashes.
3. Repair campaign role/version/recovery composition and assertion-based gates
   without broadening existing live-device authorization.
4. Resolve transient telemetry handling, canonical converter ownership and
   brittle tests; specify/test the minimal emergency fallback.
5. Obtain a follow-up independent review of the changed snapshot, addressing
   each numbered finding. No current finding is closed by this document.
6. Run exact-source public CI/native builds and seal verified artifacts once
   publication is authorized. Then perform the separate per-variant hardware
   gates, beginning with the planned PM Client canary. A source GREEN alone
   does not authorize a fleet rollout.

No source commit/push, GitHub reply, device mutation or test execution occurred
as part of this triage. Review records remain in this firmware repository.

## Remediation work after triage — unverified working tree

The table above preserves the original assessment. The following implementation
work is newer than the reviewed snapshot. **No finding is closed:** these changes
have not run in public CI, have no native artifact hashes and have not received
follow-up independent review. No hardware, live converter or GitHub publication
has been changed during remediation.

| Finding | Current implementation and intended regression evidence |
| --- | --- |
| 1 | Both non-PM release scripts now pass the exact-board guard. `test_bseed_release_inputs.py` captures the real make arguments for all four variants; corruption/boot tests exercise both boards and roles. |
| 2 | Controlled reboot and OTA apply sample/checkpoint accumulated energy, verify the NVM readback and defer reboot five seconds on failure. Network-only reset checkpoints before the SDK reset too. Full user-requested data erase remains an erase. `test_bseed_controlled_reboot.py` covers pre-periodic accumulation, failed writes/reads/readback mismatch, retry accumulation and restoration. Sudden power loss still has the ordinary checkpoint interval. |
| 3 | A separate non-PM cross-role recovery contract propagates physical confirmation through the campaign. Router source link/load checks accept only this validated context. The no-readback route remains tied to its historical exact device/images; role changes cannot inherit it for new candidates. `test_bseed_nonpm_transition.py` covers composition and refusals. |
| 4 | Link-gate assertions are explicit exceptions. The optimized subprocess test submits forged evidence and requires rejection. |
| 5 | PM Router CI selects the consolidated candidate. New non-PM native matrix code builds/rebuilds both roles using fresh identities. **Legacy non-PM workflow replacement is pending explicit approval after automatic approval review rejected removing the old CI control.** The current workflow remains incompatible with the new candidate. See the separate workflow draft. |
| 6 | PM and non-PM central release definitions pin `20260927`; actual make inputs and simulated different compiler dates have CI tests. New non-PM r9/c6 identities are reserved, unsealed; no historical hashes changed. |
| 7 | `bseed_socket_version_policy.py` requires sealed bytes, exact board/native role/build, matching transport payload and strictly increasing versions before ordinary index generation and live check/flash. Equal-version role changes remain rejected by the normal release path. A separately explicit `force_test_transition=true` exception is limited to already-custom PM/non-PM sockets, requires an exact sealed native image/SHA and permits only a private `0xFFFFFFFF` outer wrapper that changes role; it is never a release identity. Tests cover both TS011F boards and role directions. TS0726 remains outside FORCE scope. |
| 8 | The converter suppresses PM measurement decoding while a per-device OTA quarantine is acknowledged active. Release requires matching private lock/token, fresh identity evidence and the expected installed build/role. Known/unknown/stale builds are tested against the real pinned converter runtime. **Recovery follow-up remains open:** metadata-only recovery must release a matching guard, and cross-role release must use corrected post-metadata evidence rather than a possibly stale rejoin inventory. That live-tooling patch is pending approval after automatic review rejected it. |
| 9 | Text extraction starts at the definitions array. Prepared-definition tests additionally exercise actual dispatch and endpoint behavior. |
| 10 | All `bseed*.py` runtime assertions and inline native build validators have explicit exceptions; the low-level runner also retains its optimized-mode refusal. Router CI manifest checks now use explicit failures. **Assertions in the legacy non-PM workflow remain pending its approved replacement.** |
| 11 | Checked-in converter regenerated from the template. Public CI now fails on generated/checked-in drift and tests the generated file with pinned ZHC/Herdsman. |
| 12 | Minimal emergency fallback is explicitly restricted to identity fields with no GPIO mapping, and cannot be persisted as the approved board config. New C harness cases cover both boards with invalid saved and compiled defaults. |

Additional offline tests verify the four native PM/non-PM board/role candidates
and atomic native sealing. Separate FORCE-wrapper tests prove that a private
cross-role transport can wrap only an already-sealed native candidate, changes
only the outer source-role query tuple/version, and remains deployment-ineligible.
They reject stale source, wrong board/date, changed bytes, missing reproduction,
duplicate roles and invalid FORCE targets. Synthetic fixtures are never firmware
artifacts or hardware evidence.

Remaining sequence: resolve the two pending approval items; review the final
source and test diff; obtain publication authorization; run full tests/lint and
all native builds at the exact source SHA; fix CI findings; seal verified bytes;
obtain follow-up independent review; then perform separately authorized hardware
acceptance for each variant. Preserve RED/HOLD until that evidence exists.

Telemetry guard uncertainty is deliberately fail-closed. A lost option ACK or
failed OTA leaves private guard/lock evidence and suppressed measurements.
Do not delete those files, disable quarantine blindly or start another flash to
recover reporting. Inspect the current device identity and campaign first.

### Additional source/test inspection

- An explicit query-type override is now restricted to the selected board's
  registered Router/Client transport types. A same-board historical override
  remains possible, but cannot authorize the other socket board's transport.
  Added stock-wrapper, ambiguous payload-role and override rejection tests.
- Telemetry tests now exercise the actual private-file begin/release lifecycle
  with a fake bridge: lost ACK, pre-existing quarantine, wrong target, missing
  converter option, changed identity, successful archive and uncertain release.
- The emergency fallback test now calls the real persistence entry point and
  requires zero NVM writes. Non-PM transition recovery fixtures now contain a
  Router build when modeling a Router source.
- The pending PM recovery fix is specified in
  [the telemetry recovery draft](bseed_pm_telemetry_recovery_draft.md). A fresh
  ZDO descriptor with a cached Basic build is insufficient for its release
  evidence; the draft requires a matching interview and fresh inventory.

These are source changes and test definitions, not passed tests. `git diff
--check` found no whitespace errors. The required public CI/native builds,
artifact sealing, follow-up independent review and hardware gates remain open.
