# BSEED PM shared-core Router / mains-Client verification

Run `python helper_scripts/bseed_pm_variant_matrix.py` on **public GitHub Actions** in a clean Linux checkout with
Telink toolchain, SDK, host compiler and pytest installed. This is strictly offline.
`--source-only` skips compilation and cannot authorize OTA. Artifacts remain under ignored
`build/bseed-pm-role-matrix-TIMESTAMP-ID/{router,client}/`; neither image is published or flashed.

Every invocation creates new, non-overwritable evidence; use a new `--output-dir` when
selecting an explicit build path. The resulting `ROLE_MATRIX.json` is evidence for
offline gating only and must not be confused with a live hardware acceptance.

Each run tests one shared PM attribute/metering contract (types, reads, scaling,
energy accumulation, NVM, converter and live-test logic), both role-specific
host suites, then builds **both roles from one source commit**. It verifies
manifest source provenance, OTA version/type, image hash, size and configuration,
native Telink startup/version/length/CRC and the length-prefixed Basic build ID.
It repeats both clean role builds and requires identical binary hashes. The
GitHub workflow also rebuilds r19 and r21 from pinned source commits and checks
their OTA hashes. The TC32 startup check must accept r19 and reproduce r21's
flash-calibration fault before the workflow checks the candidate.
The regression also executes r19's OTA activation routine with complete,
partial, and CRC-corrupt r21 staging in both slot directions. It checks slot
selection from flash snapshots before activation and after each marker write.
This covers power loss between writes in the model. Flash programming faults
within a write need separate coverage.

Every candidate also runs **its own** activation routine, because the chip has
no bootloader and the next OTA is the only way off an installed image. The PM
and non-PM matrices stage the other role's image (the role-change path); each
TS0726 variant stages a copy of itself. All three staging cases and both slot
directions must pass, and the model rejects any instruction fetched from flash
while a flash command is in progress. The non-PM matrix applies the same
startup and activation checks as the PM matrix.

The matrix runs the native payload from each verified candidate OTA artifact
in the pinned TC32 model from both flash slots (`0x00000` and `0x40000`).
Each run must initialize the local button callback, start the watchdog and
enter the application loop three times within a fixed cycle budget.
Each slot is then restarted once on the flash contents its first start left
behind, and must reach the application loop again.
Candidate finalization requires boot and activation evidence tied to each
role's OTA hash.
The model uses empty NVM and TLSR8278 peripherals, assumes a ready crystal
with a running timer, and skips the SDK's diagnostic stack fill. It does not
model real relay outputs, flash timing, or Zigbee connectivity.
The existing host suite covers corrupt settings and safe defaults, energy
retention across a failed checkpoint and reboot, Zigbee frame-counter write
failures, and mains Client parent recovery.
The build matrix is a *candidate gate*, not equivalent to a live device read,
radio-report proof, fixture calibration or Router child-parenting verification.

The Telink 512 KiB layout accepts at most **212,992 bytes (208 KiB)** in the
complete Zigbee OTA file, including the 56-byte OTA header, 6-byte subelement
header, firmware padding and CRC. The packager rejects larger files on both
creation and resealing. The PM matrix and targeted runner also check this cap.
Use `OPT_LEVEL="-Os -fno-jump-tables"` for a new compact Telink candidate.
The build rejects `-Os` without the jump-table workaround. The consolidated
PM Router recipe already sets it. The repository records no `-Os` image that
started on real hardware: r21 is the only recorded install, and it did not
start. Until one has, treat every `-Os` candidate (PM Router, both TS0726 Router variants) as unproven beyond
the model, and prefer a wired first install on a device that already needs
physical recovery. Assign a fresh OTA version for any compiler
flag change and use the original source and flags to reproduce sealed images.

Published Router `1.2.5-bseedv8u4` (`0x12053007`) must remain byte-stable.
Current candidates are defined in `helper_scripts/bseed_pm_release.py`:
`BSEED_PM_CONSOLIDATED=1` builds draft `1.2.5-bseedr22` (`0x12053024`, type 43556),
and the PM Client build produces draft `1.2.5-bseedcli25` (same version, type 65024).
The compact PM Router disables jump tables to avoid the TC32 flash-calibration
miscompilation reproduced in r21. The r21/cli24 tuple is retired.
These recovery candidates are not sealed or hardware accepted. The preceding
cli22/r19 pair added shared parent/route policy; see `docs/bseed_link_policy.md`.
See [the four-variant review](bseed_four_variant_review_20261031.md) for this
candidate's Router recovery merge and fixes, and
[Client parent recovery corrections](bseed_client_parent_recovery_20261013.md)
for the shared recovery lifecycle and executable fault regressions.
See [the shared hardening pass](bseed_shared_hardening_20261002.md) for changes
and [Router routing hardening](bseed_router_hardening_20261003.md) for the current
candidate's separate offline and hardware gates.
The earlier r9/cli12 KitchenSocketLeft PM A/B/A canary passed the role-pair checks in
`docs/bseed_force_hardware_acceptance_20260928.md`; broader release/soak gates
remain open. This does not grant fleet release acceptance. Historical candidate modes
remain immutable evidence and are not a current-source rebuild path.
See [FORCE role-transition testing](bseed_force_test_transition.md) and the
[build/sealing procedure](../skills/bseed-zigbee-ota/references/build-and-verify.md).
Both native roles share one board-wide FILEVER: versions rise across both roles
for ordinary releases. Equal-version cross-role canary testing does not allocate
another release; it privately wraps the exact sealed destination payload with
outer `0xFFFFFFFF` and the source-role query type. FORCE wrappers are never
sealed/published fleet identities. The old PM `.17` return experiment is
retired/tombstoned only to prevent tuple reuse. No old artifact may be relabeled.

Shared code changes (including Telink PM ZCL attribute registration) must run this
matrix against every affected PM role. Client-only join/backoff changes must also
run the Client role tests; Router-only parenting changes additionally require a
real sleepy-child canary. Do not overwrite a production version for a source fix.

After offline matrix passes, a separately authorized targeted **Router** canary
may receive the verified Router OTA. Reinterview/reconcile only that exact IEEE,
verify actual ZCL reads and raw scale attributes, explicitly handle legacy
Router Zigbee2MQTT cache (voltage raw V, energy divisor 100) without blindly
copying Client metadata or rewriting HA history; configure Router reports only
if device-originated support has been demonstrated. Test no-load and known
load with independently controlled mains-safe fixture and check relay preferences,
energy monotonicity, uptime and child routing before wider deployment.

## Router rc1 abort and separately versioned rc3

The validated rc1 image (`0x1205300D`) aborted on KitchenSocketRight in the
first 1.86% of a targeted OTA on 2026-09-20; preserve its SHA/evidence as
immutable. The rc2 (`0x1205300E`) source only adds the Client's already-tested
post-abort timer/query recovery to the PM Router; it does **not** cure a
currently installed v8u4 client's initial block-response stall. rc2 was
never built: the board-wide maximum moved to `0x1205300F` (reserved
elsewhere), so rc3 (`0x12053010`) is the rebased candidate carrying the same
source fix.

Run `tests/test_bseed_pm_ota_recovery_cross_role.py` and
`tests/test_bseed_ota_abort_forensics.py` as part of this matrix. Even if both
roles compile, rc3 remains **offline-only** until target-specific OTA failure
forensics and explicit hardware eligibility are satisfied. Review
`docs/bseed_pm_router_v8u5_ota_abort_20260920.md` before considering a new
Router attempt; a new output directory or file version must never bypass its
existing `update_error` campaign lock.
