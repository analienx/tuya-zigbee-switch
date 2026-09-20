# BSEED PM Router ZCL attribute-read gate

This is a target-only **read-only** diagnostic: it sends one allowlisted ZCL attribute read, never writes configuration, toggles a relay, flashes, resets, or restarts the mesh. A read still generates Zigbee RF traffic. Use a private identity-pinned roster and private evidence paths; do not commit household identities or network details.

## Live Router / Client differentiation (2026-09-20)

- Installed PM Router `1.2.5-bseedv8u4`: independent Electrical Measurement `activePower` and Metering `multiplier` / `currentSummDelivered` reads returned `UNSUPPORTED_ATTRIBUTE` on endpoint 1. The Router read-only role audit did not receive enough separated fresh MQTT samples in 85 seconds; all four persisted PM reporting rows were outside the configured freshness policy. Settings matched the previous private baseline.
- Installed PM Client `1.2.5-bseedcli6`: the same Electrical Measurement `activePower` read was followed by fresh, non-retained MQTT measurements with no target error. A post-request MQTT message alone does **not** prove an actual read response on the radio.
- Commit `a80ac3df` (2026-09-19) added the Telink Electrical Measurement and Metering attribute registration callbacks and an offline regression test. The published `v8u4` Router source at `0d5efa6c` predates that fix. This is consistent with the Router's observed read failure; the source patch has **not** been accepted as a Router hardware fix yet.

## Repeatable bounded probe

`python helper_scripts/bseed_pm_zcl_read_probe.py --device EXACT_NAME --ieee EXACT_IEEE --expect-role Router --expect-build EXACT_BUILD --mqtt-config PRIVATE_Z2M_CONFIG --broker PRIVATE_BROKER --cluster haElectricalMeasurement --attribute activePower --seconds 14 --output PRIVATE_UNUSED_FILE.json`

Use `--expect-role EndDevice` and its exact build to compare a Client. The command refuses unsupported/mutating attributes and writes evidence only to a new path outside the repo. It classifies explicit `UNSUPPORTED_ATTRIBUTE`, other target ZCL errors, post-request MQTT observations, and absent proof separately. Nonzero exit means no clean post-request MQTT observation; **zero exit is diagnostic only**, never hardware acceptance.

## Router release gate

Do not rebuild the patched source under the already-published `v8u4` OTA identity/version and call it a new release. A Router candidate needs a distinct monotonic OTA version, reproducible manifest/source hash including `a80ac3df`, a passing direct-read probe, its own calibration/reporting/binding/settings tests, and an IKEA sleepy-child routing/parenting regression on a safe canary. Never copy the Client's cached voltage or energy divisors to a Router or silently edit historic HA energy statistics. Until these gates pass, keep automated Router PM provisioning, OTA, and fleet rollout blocked.
