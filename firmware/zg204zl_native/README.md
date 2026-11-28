# ZG-204ZL native conditional binding

Implementation for [issue #164](https://github.com/analienx/tuya-zigbee-switch/issues/164).
This is a development candidate, **not hardware-qualified firmware**. The code
runs on the sensor: Zigbee2MQTT configures it and manages standard binding rows;
it does not evaluate the lighting rules or run the fades. Once configured,
motion-to-light control works without Home Assistant. The optional time rule
needs a periodic Time response from the coordinator.

## Identity and source

- Board: **ZG-204ZL-3.0**, TLSR8258F512, upstream board 50 only.
- Base: v0148 recovery overlay, upstream
  [`c249d8a7caddb5012d06aba580d4a53d1957f9c1`](https://github.com/pvvx/ZigbeeTLc/tree/c249d8a7caddb5012d06aba580d4a53d1957f9c1).
- Candidate: **v0149**, Basic `softwareBuildID` **`3001-0149`**, OTA
  manufacturer `0x1141`, image type `0x0232`, file version `0x01493001`.
- Retains v0148 TX power, discovery scans, recovery, battery handling and PM.
  `USE_NV_APP` is unchanged. The new NV item is appended, not renumbered.
- Native image must fit **130,816 bytes** (`0x20000` minus a 256-byte reserve).
  New persistent RAM is statically limited to 512 bytes including glue reserve.

## Build and review

All builds, tests and verification run on **GitHub-hosted Actions**, from the
exact PR head SHA. Do not run these Python tests, compiler scripts, makefiles or
image verification locally or through WSL. After pushing the candidate:

```powershell
gh run list --workflow zg204zl-native.yml --branch codex/zg204zl-native-control-164
gh run view <RUN_ID>
```

The workflow records five no-feature baseline profiles, per-object maps, source
overlay hashes, and the final native ELF/map/listing and image manifest. Size
flags apply to source-built protocol code, the NV record manager and selected
software helpers. Physical flash operations, RF/CPU/ADC/PM code and prebuilt
libraries retain their original flags. `specialize_ota.py` separately pins and
specializes two SDK sources for the board's existing **OTA-client** role. It
retains client download/CRC/NV/restart code and rejects server-only requests;
the sensor cannot distribute firmware to other nodes.

Host tests compile the **same C modules** with an injected SDK boundary. They
exercise fixed wire vectors, malformed input under UBSan, real serialized
herdsman 10.10.0 Time responses, and a seeded independent controller model.
The firmware build, source guards and final image integrity are separate gates.
An artifact named `UNQUALIFIED` is developer evidence, not deployment approval.

## Configure a qualified canary

These are setup instructions for a separately authorized canary after firmware
and rollback readiness are reviewed. This implementation does not install a
converter, flash a sensor, alter bindings or enable joining automatically.

1. Load [`zg204zl_native.mjs`](../../zigbee2mqtt/converters/zg204zl_native.mjs) as a
   Zigbee2MQTT external converter. It matches only `ZG-204ZL-z` / `3001-0149`;
   older firmware keeps its built-in definition. The converter retains battery,
   occupancy, illuminance, PIR timeout, power-on behavior and OTA support.
   It is exercised with herdsman **10.10.0** and converters **26.115.1**.
2. Re-interview after installing v0149. The simple descriptor has new
   `genLevelCtrl` (`0x0008`) and `genTime` (`0x000A`) output clusters.
3. Briefly press the sensor button to open its existing **30-second fast-poll
   setup window**. Configure and read back while awake; repeat the brief press
   if needed. Holding the button is a different operation.
4. Set `illuminance_threshold` if using lux admission, then send the composite
   `native_lighting` setting below and read back `native_lighting` and
   `native_diagnostics`. The setter commits the whole record atomically and
   refuses success when readback differs. A failed readback can leave changed
   NV; inspect before retrying. Brightness readback shows the nearest Zigbee
   level: a requested 60% is stored as 152/254 and reported as 59.8%.
5. Read/set `remote_on_off: "ON"`. This is the sensor's master enable, not the
   lamp's power state. Configure before adding Level bindings: until a valid
   native record is committed, the firmware ignores Level-only rows and uses
   On/Off for a destination that has both.
6. Bind sensor endpoint 1 `genLevelCtrl` to the qualified dimmable lamp endpoint,
   or to a group containing **only Level-capable lights**. Keep On/Off bindings
   for relay-only destinations. The firmware deduplicates an address/endpoint
   or group ID and prefers Level when configured. Inspect bindings to avoid
   unwanted command destinations, including a coordinator's genOnOff report row.

Example MQTT payloads for the existing `<base>/<sensor>/set` and `/get` topics
(replace the placeholders with the user's private profile; do not commit it):

```json
{"native_lighting":{"time_enabled":true,"lux_enabled":true,"start":"18:00","end":"07:00","brightness_percent":60,"fade_in":1.5,"fade_out":3}}
```

```json
{"native_lighting":"","native_diagnostics":"","remote_on_off":""}
```

Use the converter's exposes or the existing Zigbee2MQTT binding interface; no
raw manufacturer command is necessary for normal setup. Configuration, readback
and binding are setup operations, not Home Assistant lighting automation.

## Runtime behavior

- Time and lux conditions can be enabled independently. Both enabled means AND.
  Time windows use local minutes: inclusive start, exclusive end; crossing
  midnight works. Equal endpoints are rejected when time filtering is enabled.
  Unknown/expired time denies a new time-filtered cycle; unknown lux denies a
  new lux-filtered cycle. Occupancy reporting continues independently.
- Admission snapshots brightness and fades for the cycle. Mid-cycle changes
  apply to the next admitted cycle. Motion while active does not fight manual
  dimming. Motion during fade-out plus a two-second allowance resumes the old
  cycle without rechecking the lamp's own light; later motion is a new admission.
- Absence sends OFF regardless of time or lux. The Level command is **Move to
  Level with On/Off**, target 1..254 for ON, 0 for OFF, fade in tenths of a second,
  capped at 120 seconds. No brightness ramp packets or Level server are added.
- A failed unicast OFF stays marked `off_unconfirmed`. A later denied motion
  arms another OFF **at the following absence**, only for unresolved destinations.
  It never admits ON merely to recover OFF.
- Unicast uses APS ACK, at most two lighting requests overall and one per
  destination, with two application retries at 2 and 10 seconds after failure.
  ON expires after 60 seconds; OFF retry intent expires after 30 minutes.
  Remaining fade duration is recomputed, so retries do not restart a full fade.
- Completion matching includes address, endpoint, cluster and APS counter.
  A 30-second missing callback **does not prove the stack stopped retrying**:
  its slot is quarantined and `transport_blocked` is set. Only that attempt's
  real completion releases it; there is no blind newer ON or automatic reset.
  Time requests are serialized against lighting and retain their slot until
  their real completion too, even after the three-second response deadline.
- A clock sync reads the coordinator at NWK `0x0000`, endpoint 1, then uses a
  three-second fast-poll window. The raw Time response is checked before the
  SDK parser, including source, network security, TSN, types and bounds. Master
  or Synchronized status is accepted. Refresh is at most six hours; validity at
  most twelve hours, with the documented 30-second refresh and five-minute
  validity grace around herdsman's timezone-cache expiry. Failed syncs back off
  30 s, 2 min, 10 min, 30 min, then 60 min. Turning time off stops new Time reads.
- With no native NV record, a legacy threshold `0xFFFF` migrates to lux disabled.
  Once configured, a missing threshold denies lux admission and does not silently
  revert to legacy behavior. Fresh lux ADC acquisition precedes new motion TX;
  confirms and retry handlers never acquire lux or trigger its battery check.

### Groups

Groups are **best-effort**. Their confirm proves only the first hop to the
parent; there is no per-lamp ACK, rejection or delivery guarantee. Each new
native Level ON/OFF gets one blind repeat about two seconds later. Failed
first-hop attempts get at most two application retries. Later motion does not
retry a group ON or override a manual dim. An OFF is marked unresolved only if
all its first-hop attempts failed; success suppresses later recovery OFFs.

Qualify **each lamp model by unicast** before adding it to a Level group. A relay
ignores Level commands. Do not address the same lamp through both a unicast and
a group binding, or through overlapping groups: the sensor cannot discover the
membership and cannot deduplicate those paths. Groups can start several fades
together; unicast sends may start at different times while waiting for ACKs.

## Protocol reference

Manufacturer `0x1141`, cluster `0xFC70`, endpoint 1. SET_CONFIG is cluster-specific
command 0, client-to-server, manufacturer-specific, with a fixed 12-byte payload.
Only secured coordinator-origin unicast configuration is accepted. Network
security is required; APS-layer encryption is not required. Battery voltage
below 2200 mV prevents a config NV write. An identical committed record does not
write NV again. Attributes 0 (configuration) and 1 (diagnostics) are read-only
OCTET_STR values, each containing 12 bytes excluding the ZCL length prefix.

| Config bytes | Meaning |
|---|---|
| 0 | Version 1 |
| 1 | Flags: bit 0 time, bit 1 lux; others rejected |
| 2 | ON level 1..254 |
| 3 | Reserved, must be zero |
| 4..5 / 6..7 | Start / end local minute 0..1439, little-endian |
| 8..9 / 10..11 | Fade-in / fade-out, little-endian tenths, 0..1200 |

| Diagnostics bytes | Meaning |
|---|---|
| 0 | Version 1 |
| 1 | Phase in bits 0..1, last decision reason in bits 2..7 |
| 2 / 3..4 | Clock valid / age in minutes, little-endian, saturated |
| 5 / 6 | Last APS / ZCL status; `0xFF` means not observed |
| 7 | Bits: 0 configured, 1 OFF unconfirmed, 2 stale ON dropped, 3 transport blocked |
| 8 / 9 / 10 | Selected unicast On/Off / unicast Level / group destinations |
| 11 | Last ZCL command; high bit distinguishes Level cluster 8 from On/Off cluster 6 |

Reasons: 0 admitted, 1 master disabled, 2 clock unknown, 3 outside time window,
4 lux threshold missing, 5 bright/invalid lux, 6 resumed fade, 7 no binding
(reserved). APS `0xF1` is the local missing-completion marker, not an SDK result.
APS success is **not proof of lamp state**. Default Response errors such as
`0x87` are retained separately; group-addressed lights do not return these.

## Hardware acceptance still required

Keep the PR draft and issue open until a separate canary establishes:

- Coin-cell cold boot, ordinary motion/reporting and a legacy On/Off-only binding.
- Confirm address mode, address resolution and APS-counter behavior on this SDK;
  missing completions and timer-pool pressure must be observable and recoverable.
- Level 0 truly turns each lamp off; a subsequent plain On restores an acceptable
  brightness. Check error responses and abrupt/manual dim interactions.
- Real-parent Time response success, expiry/recovery, and RC clock drift with a
  temperature step. Host DST vectors do not measure oscillator accuracy.
- Lost OFF with the lamp power removed mid-cycle, later restored; bright motion
  must recover at absence. Check weak-battery behavior without unsafe supply tests.
- Group first-hop behavior, one blind repeat, overlapping-path avoidance, and
  fade start spread with four or more lamps compared with unicast.
- Authorized OTA download and post-boot identity on the final exact candidate.
  A green CI build, OTA 100%, or `status:ok` does not confirm a boot.
