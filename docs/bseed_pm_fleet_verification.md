# BSEED PM Client and Router: serial fleet verification

The read-only fleet runner `helper_scripts/bseed_pm_fleet_audit.py` audits **one verified, named PM target at a time**. It accepts an explicit private JSON roster and creates a new private evidence directory with one target JSON per device plus `SUMMARY.json`. It never flashes, configures, resets, opens permit-join, switches a relay, or controls a test load. It is an inspection tool, **not** a broad deployment authorizer.

## Private roster and invocation

Store the roster outside git with four connection fields: `mqtt_config` (private Zigbee2MQTT YAML), `broker`, `ssh_host`, and `ssh_key` (host-key-verified SSH private-key path). Supply `targets` as a JSON array of `{ "name": "FRIENDLY_NAME", "ieee": "0xEXACT16HEX", "role": "Router", "build": "EXACT_INSTALLED_BUILD", "baseline": "PRIVATE_PRIOR_AUDIT.json" }`. For an EndDevice set `role` to `EndDevice`; a first audit may omit `baseline`, but settings persistence remains **unverified** until a same-IEEE baseline is compared. No credentials, private roster, IEEE or actual addresses belong in the public repository.

```sh
python helper_scripts/bseed_pm_fleet_audit.py \
  --roster PRIVATE_ROSTER.json \
  --output-dir NEW_PRIVATE_EVIDENCE_DIR \
  --observe-seconds 85
```

The tool refuses duplicate IEEE/name, unknown role, missing required fields, an existing output directory, or files under the repository. It audits targets serially to avoid simultaneous Zigbee requests. Each failed target remains `unconfirmed`; the runner continues read-only through the explicitly listed targets and exits nonzero when any fails. No retry or recovery action is implicit.

## Independent release gates

| Gate | Read-only fleet audit | Separate physical/operational acceptance |
|---|---|---|
| IEEE, installed build, live Zigbee role, endpoint layout | Checked | Role transition and physical relay behavior still require independent evidence |
| Cached metering scales, coordinator binding and configured reporting | Inspected by firmware role | Device-originated reads and actual raw reports must corroborate cached data |
| Live MQTT | Non-retained sample plausibility, timing and cumulative energy checked | Per-attribute freshness, reference-load accuracy, change-to-zero latency and HA entity parity not proven by a combined MQTT state |
| Persistent settings | Cached settings compared with same-IEEE baseline | Rejoin/power-cycle persistence not established unless a safe, independently controlled test was performed |
| Router network role | Live ZDO Router descriptor | Sleepy child retention, parent keepalive, route recovery, coexistence and topology must be tested independently |
| Mains Client role | Live ZDO EndDevice descriptor | Always-awake parent lease, repeated relay reads, rejoin/recovery and stable network operation must be tested independently |

## Router scale-read finding and stop condition (20 September 2026)

A single targeted PM Router `seMetering.read([multiplier, divisor, currentSummDelivered])` returned `UNSUPPORTED_ATTRIBUTE`. Follow-up bounded single-attribute reads of `multiplier` and `currentSummDelivered` separately also returned `UNSUPPORTED_ATTRIBUTE`. This is **not** a proven network timeout or proof that Router energy reporting itself is absent; a fresh attribute-report frame and a read response are different ZCL operations. The persisted legacy Router energy divisor differs from the Client divisor. Do not infer installed Router scaling from current Client source, copy scaling metadata into the Zigbee2MQTT database, or enable Router-wide automatic configure.

Investigate the exact installed Router build's cluster registration, attribute IDs/types, endpoint mapping and read-handler path against the converter and actual Zigbee responses. Require successful device-originated scale/measurement proof or a documented, device-specific alternative before implementing Router provisioning. A single unsupported read must be captured once and then stop; do not hammer production Router devices with repeated reads.

The physical acceptance runner `tests/live_bseed_pm_metering.py` requires an independently identified and explicitly authorized load fixture. A previous passive zero or any recorded metering state cannot replace actual loaded-to-unloaded transitions. Use `--require-ha` when Home Assistant sensor parity is part of release acceptance. A controlled fixture, safe load rating and authorization must be established before execution; the fleet audit never attempts to discover or switch a household appliance.
