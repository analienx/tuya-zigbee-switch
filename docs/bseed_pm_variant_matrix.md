# BSEED PM shared-core Router / mains-Client verification

Run `python helper_scripts/bseed_pm_variant_matrix.py` from a **clean Linux** checkout with
Telink toolchain, SDK, host compiler and pytest installed. This is strictly offline.
`--source-only` skips compilation and cannot authorize OTA. Artifacts remain under ignored
`build/bseed-pm-role-matrix/{router,client}/`; neither image is published or flashed.

Each run tests one shared PM attribute/metering contract (types, reads, scaling,
energy accumulation, NVM, converter and live-test logic), both role-specific
host suites, then builds **both roles from one source commit**. It verifies
manifest source provenance, OTA version/type, image hash, size and configuration.
The build matrix is a *candidate gate*, not equivalent to a live device read,
radio-report proof, fixture calibration or Router child-parenting verification.

Published Router `1.2.5-bseedv8u4` (`0x12053007`) must remain byte-stable.
Opt-in candidate mode `BSEED_PM_ROUTER_CANDIDATE=1` builds isolated
`1.2.5-bseedv8u5-rc1` (`0x1205300D`, Router image type 43556).
The Client remains `1.2.5-bseedcli6` (`0x1205300C`, Client type 65024).
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
