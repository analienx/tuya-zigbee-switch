# Independent firmware review — issue 55

Published request: https://github.com/analienx/tuya-zigbee-switch/issues/55

Request an independent, comprehensive code review of the BSEED firmware family and its build/deployment tooling before advancing the consolidated candidate. The reviewer should challenge the existing conclusions, identify omitted fixes and regressions, and give a separate verdict for every hardware/role combination.

## Scope: all four variants

| Board | Role | Native OTA image type | Review focus |
| --- | --- | --- | --- |
| PM `b28wrpvx / TS011F-BS-PM` | Router | 43556 | Metering/control, routing and child parenting |
| PM `b28wrpvx / TS011F-BS-PM` | Mains Client / EndDevice | 65024 | Metering/control, continuously enabled receiver, parent retention/recovery |
| Non-PM `o1jzcxou / TS011F-BS` | Router | 43555 | Correct board pins, control, routing and child parenting |
| Non-PM `o1jzcxou / TS011F-BS` | Mains Client / EndDevice | 65026 | Correct board pins, control, continuously enabled receiver, parent retention/recovery |

PM and non-PM are different hardware/configuration/image lines. Client means a non-routing, mains-powered EndDevice; it must not silently become a sleepy battery device. Power-management `PM_ENABLE` and power-metering PM are unrelated concepts. Review shared changes against all four combinations; a PM Client pass does not validate non-PM or Router behavior.

## Source under review and important limitation

- Working branch: [`fix/bseed-nonpm-client-keepalive-cli5`](https://github.com/analienx/tuya-zigbee-switch/tree/fix/bseed-nonpm-client-keepalive-cli5).
- Current local base: [`cd46953dd37210bf0b01f61977e92be2a0e39a57`](https://github.com/analienx/tuya-zigbee-switch/commit/cd46953dd37210bf0b01f61977e92be2a0e39a57).
- **The latest consolidated candidate is still uncommitted and unpushed.** Public branch contents alone do not include the full candidate. An executor with access to the working checkout must inspect tracked changes and untracked candidate files. A remote-only reviewer must explicitly mark candidate review incomplete until an exact candidate snapshot/commit is supplied; do not issue a candidate GREEN against the old base.
- Proposed PM identities: Client `1.2.5-bseedcli11`, Router `1.2.5-bseedr7`, both `0x12053014`; experimental return Router `1.2.5-bseedr8`, `0x12053015`. These are candidates, not accepted releases. No new non-PM artifact is currently proposed; assess shared-code regressions and the separate non-PM release/build path.
- No fresh public CI/native builds or hardware acceptance have run for the uncommitted candidate. Earlier green runs do not cover it.

Local candidate entry points (not yet available on the public branch):

- `docs/bseed_pm_consolidated_cli11_20260927.md` — candidate, evidence corrections and both internal review sweeps.
- `docs/bseed_kitchenleft_client_acceptance_plan_20260926.md` — proposed canary and acceptance sequence.
- `helper_scripts/bseed_pm_release.py`, `helper_scripts/bseed_pm_seal.py` — identities, native image verification and sealing.
- `skills/bseed-zigbee-ota/references/build-and-verify.md` — updated executor workflow.

Also read `AGENTS.md`, `skills/bseed-zigbee-ota/SKILL.md`, the committed device defect/role/deployment records, firmware source, converter template, build scripts, workflows and tests. Read evidence critically: documentation and tests can repeat the same wrong assumption as the implementation.

## Required review areas

1. **Reachability and recovery.** Trace startup, receiver-on settings, disabled MCU sleep, descriptor flags, parent keepalive, polling retries/drift, join/rejoin/parent-loss state machines, timer ownership, OTA fast-poll entry/exit/abort and SDK interactions. Investigate the failure where uplink telemetry continues but downlink commands/read requests stop working. Distinguish converter dispatch failures from actual radio timeouts. Identify any remaining timer, lifecycle, retention or recovery gaps rather than assuming current fixes solve the RF cause.
2. **PM attribute and reporting contract.** Trace actual EP1 attribute registration, types, access and SDK handlers; all four measurements and eight multiplier/divisor attributes; configure/read-reporting responses, binds, periodic and change-triggered reports, zero-load transition and cumulative energy. Distinguish wire replies from caches/MQTT. Inspect scale conversion before configure and with empty/stale caches, report thresholds in raw units, and legacy calibration compatibility. Ordinary configuration should not require a manual repair command after every flash.
3. **Relay/button behavior and persistence.** Correct board-specific GPIO maps; EP2 control/read selection; physical versus logical relay mode; direct binding; debouncing; valid startup policies; safe handling of corrupt saved configuration/settings; NVM migration bounds, energy/calibration retention and fallback data loss. Flag regression risks for both hardware boards and roles.
4. **Router behavior and topology.** Child keepalive support, parenting/timeout behavior, rejoin compatibility, routing and broadcast load. Review whether converting selected routers to Clients preserves required coverage and capacity, while clearly separating code findings from topology hypotheses. Do not recommend a network rebuild without evidence.
5. **OTA identity and apply path.** Same-role and cross-role updates, queried image type versus actual ZDO role, native payload versus wrapper, monotonic board-wide versions, rollback/recovery limitations, startup markers/size/CRC, and retained settings. Transfer completion is not proof of boot. Inspect the complete Basic build name and length prefix, the 16-byte limit, manifests and postflash comparisons; no substring/truncation workaround or stale-cache acceptance.
6. **Build/tooling and release gates.** Variant separation; actual compiler flags; sealed identity allocation; fixed build date; reproducible clean native builds; artifact verification/sealing; runner assertion/optimization behavior; CI coverage and tests that accidentally mock away defects. Review generated converters against the pinned deployed libraries, including selection of plain `state`, scoped `state_relay` and availability reads. Review whether skills/scripts are sufficiently accurate for another executor to build the next version correctly.

## Evidence corrections to preserve

- Fresh rc5 wire evidence showed an overlength 19-byte Basic name; rejection and stale cached cli8 were not proof of a missing version bump.
- Workroom status 139 was from **Read Reporting Configuration / NOT_FOUND**, not an unsupported Read Attributes response (134). This does not establish that native attribute reads work; it corrects the earlier interpretation.
- OTA 100%/`status: ok`, a matching OTA query version, current MQTT telemetry and a cached `online` flag are each insufficient for complete firmware/hardware acceptance.

## Requested deliverable

Post findings here, ordered by severity, with file/line references, triggering conditions, causal explanation, confidence, affected variants, a proposed correction, and a meaningful regression test or hardware experiment. Include missing tests and counterexamples even if existing CI would be green.

Provide a four-row verdict table: **GREEN / AMBER / RED / NOT REVIEWED**, with blockers and evidence per variant. Separate source-review readiness, exact-SHA CI/native-build readiness and hardware/fleet readiness. List reviewed commit plus candidate snapshot/diff identity, toolchain/dependency versions, checks actually run and limitations.

A green independent source review is permission to consider the next build/validation stage, not a claim of fleet deployability. Acceptance still needs exact-source public CI and the appropriate per-variant hardware gates. This issue authorizes review and reporting only: no firmware flash, relay operation, production converter/network change, fleet rollout or source publication is requested from the reviewer.
