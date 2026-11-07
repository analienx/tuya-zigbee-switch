# PR #114 software acceptance

On 2026-11-07 the owner confirmed that there is no noncritical hardware,
requested software acceptance for this PR, and specified that hardware
validation will occur through production deployment. This supersedes the
earlier requirement to keep the source PR in draft until physical trials.

## Review and merge gates

- Public GitHub-hosted CI must pass on the exact candidate source SHA.
- The complete host suite must exercise production recovery paths, SDK owner
  guards, delayed callbacks, repeated outages, reporting hold and flush,
  persistent budgets, corrupt records, and frame-counter write failures.
- PM Router and mains Client must build reproducibly from that SHA and boot
  their exact OTA payloads from both slots in the pinned TC32 model.
- The boot regression must accept the archived r19 bytes and reject the
  archived r21 flash-calibration defect. OTA activation tests must cover
  partial and CRC-corrupt staging and power loss between marker writes.
- Both non-PM roles, the paired Router contract, and the TS0726 variants must
  pass their build and identity checks. Both TS0726 Router candidates and the
  mains Client must boot their exact OTA payloads from both slots.
- Candidate finalization must depend on the TS0726 startup workflow as well
  as the existing role matrices, host suite and hardening checks.

After these gates pass, mark PR #114 ready for review. Hardware observations
are recorded as unmeasured, with production validation tracked separately.

## Evidence limits

The TC32 model uses TLSR8278 peripherals with 512 KiB flash and empty NVM.
It assumes a working crystal and skips the SDK diagnostic stack fill.
The host suite supplies synthetic NVM and SDK callback events.

Production deployment will establish actual relay continuity, metering,
button response, RF recovery, bindings, child retention and persistence.
The prior [physical protocol](RECOVERY_PR114_PHYSICAL_ACCEPTANCE.md) is a
reference for recording observations when suitable instrumentation is available.
Its spare-device and logger requirements are superseded for PR readiness.

The SDK provides untagged callbacks. Source guards and synthetic tests cover
the documented contract, while production behavior remains to be observed.
The B5-to-B6 budget migration preserves consumed credits. Historical r19
rejects B6 and disables automatic rescue reboots, so that downgrade remains an
operator recovery decision rather than automatic rollback.

The Table Left r21 incident remains unresolved at the device level.
These source changes and CI results provide no physical recovery evidence.
