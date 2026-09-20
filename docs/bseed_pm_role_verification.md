# BSEED PM verification: Router and mains Client

Scope: only the verified `b28wrpvx` / `TS011F-BS-PM` custom PM sockets with endpoint-1 meter and endpoint-2 relay. Non-PM sockets, dimmers, Tongou breakers, stock Tuya PM sockets and unidentified builds need separate device contracts. This is a **read-only audit**, not a fleet acceptance shortcut.

## Verified canary findings (2026-09-20)

- Client the newly provisioned PM Client canary / `cli6` was provisioned with power 10/60/5, current 5/300/50, voltage 5/300/5, energy 10/600/1 (minimum seconds / maximum seconds / raw threshold). Its later passive idle observation and strict postflash checker passed provisionally; fixture load-to-zero, known-load calibration, relay behavior and long-term network stability remain open.
- Both Router `v8u4` canaries (two independent PM Router canaries) persist 3600-second maximum reporting for all four PM attributes; `activePower` threshold is 10 W. Each failed the 85-second passive freshness audit. This does not invalidate the separate prior sleepy-child routing validation.
- Both Routers store `rmsVoltage` near 237-238 raw units and cached `seMetering.divisor=100`, whereas `cli6` uses centivolts and energy divisor 1000. Router voltage multiplier/divisor were not in those Zigbee2MQTT cache records. **Never copy Client scaling constants or calibrations to Router**; infer actual Router scaling only from a device-originated scale read and converter evidence. Do not modify historical HA energy statistics automatically.

## Commands and gates

`python helper_scripts/bseed_ota_campaign.py --profile PRIVATE_PROFILE.json --mode audit-pm` runs read-only role/build/identity/ZDO checks, endpoint/binding checks, per-role cached scaling checks, passive MQTT freshness/plausibility, reporting policy, and a snapshot of persisted configuration settings. No relay commands, reflashes, Zigbee restarts, `device/configure`, or per-device reporting writes are performed by this command. Profile's `pm_audit_seconds` overrides the default 85-second passive window.

`--mode provision-pm --confirm-ieee EXACT_IEEE` is **Client-only**. An in-progress PM Router `--mode flash`, `transition`, or `rejoin` is blocked before OTA transport if the profile requests automatic PM provisioning: role-specific Router repair and acceptance have not yet been implemented/validated. For an existing Router, use `audit-pm` without writes. Do not set `require_pm=false` to bypass the release gates.

All JSON evidence lives in the private campaign workdir. Never commit private device identities, network topology, Zigbee2MQTT database backups, MQTT credentials, real local addresses or appliance load profiles.

## Settings persistence, fixture, and Router-specific checks

A first read-only audit records `settings_snapshot` in its private JSON evidence. Subsequent audits may pass that exact evidence path with `--baseline PRIVATE_OLD_AUDIT.json`, or specify `pm_settings_baseline` in the private campaign profile. Baseline comparison pins the same IEEE; any changed or missing recorded setting fails closed. Volatile relay `onOff`, `onTime`, and `offWaitTime` are intentionally not compared as preferences. An absent earlier baseline is recorded as **not verified**, never silently counted as a persistence pass. Snapshot covers available cached power-on, switch/button, indicator and other cluster settings; it is not a substitute for a device-originated read or testing behavior after a controlled restart.

The read-only audit also records the persisted endpoint-1 coordinator bindings and metering reporting rows, but does not claim those persisted rows are still active on the radio after a rejoin. Independently validate live reporting, settings persistence and actual relay behavior using a verified safe fixture. For a Router, preserve child-parenting/routing and verify sleepy-device actions after an idle/sleep interval plus topology and recovery. For an EndDevice, verify always-awake parent/rejoin stability. No automated factory reset, breaker toggling, load activation, OTA, or Router role conversion is authorized by the audit.

The shared physical load test is `tests/live_bseed_pm_metering.py`: a dedicated, independently controlled and appropriately rated fixture must be validated before use, with no control of the DUT relay or its power supply. The test runner needs a baseline and new non-retained MQTT load-to-zero observations, energy monotonicity and the existing HA power entity when requested. It cannot establish firmware accuracy from cached values alone. Without that fixture, keep the device `unconfirmed` for full hardware acceptance even if its read-only audit passes.

**Additional canary comparison:** The new role-aware read-only audit passed the newly provisioned PM Client canary as `read_only_audit_candidate` with seven passive messages and no report gaps. the earlier PM Client canary yielded two messages but failed due to out-of-policy current, voltage and energy reporting rows. The two `v8u4` Router audit windows failed the passive freshness gate with all four PM reporting rows outside policy. The audit did not alter any of these devices.
