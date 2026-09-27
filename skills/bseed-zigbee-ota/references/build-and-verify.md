# Reproducible BSEED socket candidate workflow

Current status: independent review HOLD. Read the remediation table in
`docs/bseed_independent_review_triage_20260927.md`; the non-PM workflow replacement
and PM telemetry-recovery follow-up are not yet applied. The procedure below is
the required completion path, not evidence that CI or hardware already passed.

Run from repository root. This is the offline build path; live campaign gates
remain in the main skill. Do not flash, reset or modify a production converter
as a side effect of building.

1. Read current branch status, the latest device investigation, the identity
   registry and `docs/bseed_pm_consolidated_cli11_20260927.md`. Compare actual
   commit ancestry/diffs: a larger cli number does not imply every earlier
   branch's fixes were merged. Preserve another agent's uncommitted work.
2. Allocate before editing: use `bseed_ota_identity.py suggest-next --image-type
   65024` and `emit-make-vars --image-type 65024 --version-str SHORT_BUILD_ID`.
   New Basic IDs must be 1..16 ASCII bytes. Update the single definition in
   `helper_scripts/bseed_pm_release.py`. Router and Client share the next
   board-wide version, with separate image types 43556/65024. Allocate the
   experimental return at a strictly greater version and a different build ID.
   Never reuse a sealed tuple for changed bytes, even for a one-line fix.
   Pin `RELEASE_DATE` for that candidate set. Both scripts pass it as
   `BSEED_BUILD_DATE`; compiler `__DATE__` must not change sealed bytes on a later
   rebuild. Do not change the pinned date after sealing.
   For non-PM TS011F use `bseed_nonpm_release.py`, Router type 43555 and Client
   type 65026, and allocate against that board's own maximum. Both actual native
   scripts must pass `DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM`. PM and non-PM
   are separate images; power-management `PM_ENABLE` is unrelated to metering.
   Equal numeric versions cannot role-transition: same-release r7/cli11 or
   r9/c6 needs a separately allocated strictly newer destination to change role.
3. Reserve the shared version first. Do not reserve a higher return version
   before first-build shared-role artifacts are verified/sealed: the monotonic
   board gate would correctly reject unsealed lower-version bytes. Historical
   sealed builds may rebuild identically; do not bypass with allow-downgrade.
4. Implement narrowly supported fixes and regression tests. The PM socket's
   canonical GPIO configuration must survive malformed/wrong-board NVM.
   Preserve valid startup preferences, including PREVIOUS=255. Use executable
   host tests for polling failures/rejoin, not only source-string assertions.
   Firmware attributes and cached converter metadata are separate contracts.
5. Once publication is authorized, commit and push. Require `test`,
   `BSEED PM role matrix`, the approved non-PM role matrix and Router CI green on
   that exact SHA. CI runs full host tests, pinned real ZHC runtime tests,
   C formatting, identity gates, native Telink Router+Client builds and an
   independent clean rebuild of each role with identical hashes. The return
   package is also built from that SHA. Local compilation is not release proof.
   If lint fails, use the CI `lint-diagnostics` artifact, inspect the patch,
   then commit and rerun. A corrected source SHA needs its own green runs.
6. Download the role-matrix, experimental return and generated converter
   artifacts into ignored `build/ci-SHA/`. Check successful GitHub conclusions
   and their `headSha`; a downloaded report is not authentication of CI success.
   Run:

   ```text
   python helper_scripts/bseed_pm_seal.py --matrix-dir MATRIX_DIR --return-dir RETURN_DIR --nonpm-dir NONPM_MATRIX_DIR --source-commit FULL_SHA
   ```

   This verifies clean-source manifests, actual OTA/native versions, length,
   startup marker, CRC, exact embedded length-prefixed Basic ID, role hashes,
   reproducibility report and byte-identical Router payload in the return
   wrapper. It does not claim the firmware booted or the downlink works.
7. Once exact-SHA CI is green, repeat with `--write`. Review and commit the
   registry diff; no old sealed hash may change. The tool seals all six tuples
   atomically and is idempotent for identical artifacts. Run CI again on the
   registry commit, verifying unchanged binaries pass the sealed identity gate.
   Record source SHA, run URLs, filenames, SHA-256/512 and pending hardware
   gates in the release document. Never commit downloaded binaries or logs.
   The six tuples are four native board/role variants plus the PM return native
   and Client-header wrapper. A transport wrapper's registry line does not
   establish its payload role; preserve explicit payload-role metadata.

## Converter and hardware distinctions

CI uses the deployed Z2M 2.14.0 dependency pair: ZHC 26.103.0 / Herdsman 10.9.1,
locked under `tests/zhc_runtime/`. Update the pin deliberately when production
changes. Test the generated converter with those real libraries. Fixed PM
scales are selected only for explicitly supported board/model/build contracts;
unknown and legacy v8u4 builds must not receive Client scales automatically.
Test reports arriving before configure, as well as empty/stale-cache configure:
scaling only inside configure leaves an early-report race. Compare the real
library-generated raw reporting thresholds against the Python provisioner;
its explicit change overrides are raw units, while its defaults use scaled units.
Use the generated CI converter artifact. Preserve original calibration/cache
snapshots and avoid applying scale corrections twice.

Before a supported custom-PM OTA the candidate converter must advertise the
telemetry-quarantine option. The campaign requires an acknowledged option change
before submitting the image. Measurements remain suppressed until matching
campaign/identity evidence permits release. Unknown or stale build metadata must
not decode newly flashed raw measurements with legacy divisors. Lost ACKs,
failed transfers and incomplete metadata recovery require inspection, not file
deletion, another flash or a blind option reset. Current recovery gaps are in
the triage; do not deploy this incomplete path. Stock-to-custom PM conversion
cannot assume the stock converter supports this quarantine contract.

Runtime safety checks must use explicit exceptions, including Python inside
shell scripts and workflows. Test negative evidence under `python -O` as well
as normal Python. Critical assertions silently disappear under optimization.
Keep historical artifacts verified against their sealed hashes; changed source
must build fresh candidate identities, never masquerade as a historical baseline.

Green builds cannot prove radio timing, physical metering, retained energy,
relay output, fresh-join provisioning, parent recovery or an upgrade apply path.
The legacy PM migration's bounded fallback may discard corrupt legacy records;
never promise energy retention for that fallback. Follow the KitchenLeft plan:
short cli6 readiness baseline, authorized upgrade, fresh installed identity,
then full firmware+converter acceptance and a small multi-parent pilot.
Source fixes in cli11 cannot improve the old firmware while it downloads cli11.

For mains Clients, enforce EndDevice + Rx-on + power management disabled in the
effective stack configuration, not only by inspecting makefile text. Keepalive
polling is still needed for parent retention; it does not mean a mains socket
is intended to sleep between polls. Test fast-mode setup failure, rate drift and
rejoin preservation. Uplink telemetry cannot certify working downlink.

Test converter selection as well as callback execution: Z2M's MQTT publish path
filters endpoint-scoped converters before calling `convertGet`. Test plain
`state`, `state_relay` and availability reads against the pinned library, and
keep unsolicited-report observations separate from diagnostic GET traffic.
For PM acceptance, require fresh raw reads of all four measurements and all
eight scale attributes; seeded converter cache values are not firmware proof.
Never truncate a long Basic build name: allocate a unique short name and verify
that same complete string in firmware, manifest and postflash wire identity.
