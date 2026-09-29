# Independent executor review request

Published comment: https://github.com/analienx/tuya-zigbee-switch/issues/55#issuecomment-5853080736

Please perform a fresh, independent full code review of **all four variants: PM Router, PM Client, non-PM Router and non-PM Client**, including shared core, SDK integration, converters, build scripts, Python deployment/verification tools, tests and executor skills.

Do not limit the review to the latest diff or assume our internal findings are correct. Trace the end-to-end paths and look for fixes that exist only on another branch, tests that validate their own mocks, and behavior that is documented but never enforced. In particular, challenge the missing-attributes/reporting/scaling fixes and the case where a Client still reports telemetry but stops responding to commands. Verify no-sleep/Rx-on configuration, parent retention and recovery, OTA polling/application, and the firmware-name length/identity handling.

**Before starting, pin what you can actually review.** The consolidated candidate is currently local and uncommitted. If you have that checkout, include tracked and untracked candidate files and record its snapshot identity without disturbing it. If you only have GitHub access, review the published base but mark the candidate NOT REVIEWED and state exactly what source is missing. A green verdict for the old commit cannot approve the local candidate.

Please return:

1. Prioritized, independently substantiated findings with concrete failure scenarios, source references, affected variants and suggested fixes/tests.
2. A verdict for each of the four variants and explicit blockers before progressing.
3. Remaining CI/native-build and hardware evidence requirements, including sparse downlink checks after idle, parent-loss recovery, unsolicited reporting, loaded-to-zero metering and retained settings/energy.
4. Your broader technical assessment: whether this is a sound consolidated release approach, what we have missed, and the smallest effective sequence to reach a deployable result.

Post the review and recommendations on this issue. We will assess your findings before moving ahead. Keep source-review approval, successful CI and hardware/fleet acceptance separate; do not mutate devices or publish source as part of this review.
