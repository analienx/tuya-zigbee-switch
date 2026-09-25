# BSEED PM shared-core Router / mains-Client verification

Run `python helper_scripts/bseed_pm_variant_matrix.py` from a **clean Linux** checkout with
Telink toolchain, SDK, host compiler and pytest installed. This is strictly offline.
`--source-only` skips compilation and cannot authorize OTA. Artifacts remain under ignored
`build/bseed-pm-role-matrix-TIMESTAMP-ID/{router,client}/`; neither image is published or flashed.

Every invocation creates new, non-overwritable evidence; use a new `--output-dir` when
selecting an explicit build path. The resulting `ROLE_MATRIX.json` is evidence for
offline gating only and must not be confused with a live hardware acceptance.

Each run tests one shared PM attribute/metering contract (types, reads, scaling,
energy accumulation, NVM, converter and live-test logic), both role-specific
host suites, then builds **both roles from one source commit**. It verifies
manifest source provenance, OTA version/type, image hash, size and configuration.
The build matrix is a *candidate gate*, not equivalent to a live device read,
radio-report proof, fixture calibration or Router child-parenting verification.

Published Router `1.2.5-bseedv8u4` (`0x12053007`) must remain byte-stable.
Opt-in candidate mode `BSEED_PM_ROUTER_CANDIDATE=1` builds isolated
`1.2.5-bseedv8u5-rc3` (`0x12053010`, Router image type 43556).
The Client is the golden `1.2.5-bseedcli8` (`0x12053010`, Client type 65024).
Both roles share one board-wide FILEVER: versions rise across both roles of
a board, so a new release moves Router and Client together.
Do not use either image as the other role or put the experimental Client in a
normal OTA index. No old Router artifact may be relabeled with a newer version.

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
