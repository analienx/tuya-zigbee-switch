# BSEED two-slot OTA coordinator pilot — 14 November 2026

**Status: software prototype, not live-flash approval.** Normal BSEED behavior remains one OTA per network. This opt-in, paired, same-role pilot changes only OTA admission, not firmware, Zigbee2MQTT configuration or the public firmware index.

## Production evidence

- Zigbee2MQTT **2.14.2**, official add-on 2.14.2-1; exactly one radio owner.
- USB **SMLIGHT SLZB-06P7U / CC2652P7**, Z-Stack at 115200, coordinator IEEE `0x00124b002d12b1fd`.
- Actual installed Zigbee2MQTT `otaUpdate.js` tracks an `#inProgress` **Set keyed by device IEEE**. Zigbee-herdsman `Device.updateOta` creates one `OtaSession` per device. Its ZCL waiter is scoped to NWK address, endpoint and cluster command; `#lastBlockResponseTime` is per session. It can support parallel independent device updates, but no global network-wide response-pacing budget is implemented there.
- BSEED already has `concurrent_ota_ieees` for the OTA active-device gate, but the old shared singleton lease prevents the second BSEED campaign. This branch adds optional two-slot admission while preserving the original default.
- **Do not adopt a running legacy campaign:** at 18:37 KitchenSocketRight was mid-OTA at 37.44% under a singleton lease. This two-slot implementation refuses admission until that legacy lease is independently terminalized and released.

## Target identification

- **`WRSocketEntrance`**, IEEE `0xa4c1387b13e9436d`: non-PM `o1jzcxou / TS011F-BS` Router, installed `1.1.3-bseedr18`. Sealed target `1.1.3-bseedr19`, image type 43555, file version `0x1102301E`, OTA SHA256 `3a85623ab51e535b38384e41bedc13cf961721e36b62bb462419ce1b318b5f71`.
- **Possible second pilot partner:** `WorkroomSocketCabinet`, IEEE `0xa4c138c5f07ee732`: PM `b28wrpvx / TS011F-BS-PM` EndDevice, installed `1.2.5-bseedcli22`, target `1.2.5-bseedcli25`, OTA SHA256 `4246f097a8927fcfa9a02d16d7ee966cc96ea14487681b2cd9f34b50b41dc914`. It is *not* the entrance socket.
- Non-PM recovery blocker: the existing entrance exception allows only *v8 → r18*. The newer `r18 → r19` requires a fresh explicit physical-unload confirmation and board-specific tested SWire readback/recovery evidence or an independently reviewed, exact-device exception. A concurrency grant does **not** waive this protection.
- Stock Tuya conversions, cross-role/FORCE wrappers, failed/offline devices, and devices powering critical infrastructure are excluded from the first test.

## Lease design

Legacy exclusive lease: `bseed-zigbee-<network-hash>.json`.

Optional per-device files: `bseed-zigbee-<network-hash>.slot-<ieee>.json`.

Both paths use a short-lived, atomic same-filesystem directory gate: `bseed-zigbee-<network-hash>.admission-lock`. A crashed/orphaned admission gate is a fail-closed manual-investigation condition; the gate is **not** the long-held OTA lease.

Two-slot configuration in each PRIVATE profile must be reciprocal:

- `"parallel_ota_max": 2`
- `"concurrent_ota_ieees": ["<OTHER_DEVICE_FULL_IEEE>"]`
- `"source_rejoin_strategy": "none"` and `"allow_join_all_fallback": false`

A and B must list each other, use the same coordinator network ID and exact private lock directory, and remain same-role non-FORCE OTA. A third device, repeat IEEE, wrong reciprocal peer, mismatched network or simultaneous old singleton is refused. Each device retains its own image SHA, transaction, active campaign lock, telemetry quarantine, state, error evidence and independent terminal release.

For initial dual pilots, run the supervisor with **`--max-attempts 1`**. Never auto-open permit join, restart coordinator, source-rejoin, initiate a second image, or retry an error while the other OTA owns a slot. A failed campaign must first be independently reconciled with its peer no longer updating.

## Effective first experiment

1. After KitchenSocketRight completes, verify its build/Router/relay/PM and release the *old* lease. Do not rewrite any active lease.
2. Select two exact custom-firmware same-role sockets whose loads are disconnected/verified safe. For WRSocketEntrance, the additional non-PM recovery evidence is mandatory. Prefer independent radio paths; inspect cached topology and passive routes, **not** a mass map scan during transfer.
3. Snapshot: coordinator and host versions, exact firmware SHA/version, target source builds, source ZDO, relay and settings baselines, LQI, 10–15-minute Z2M and coordinator error baseline, and ordinary control latency. Keep the coordinator's single USB owner; do not attach a second serial reader.
4. Stage **two private one-entry OTA indexes**, one for each exact IEEE/image. Validate the matching native/role payload, `prepare`, fresh `preflight` and read-only `check` independently before starting either. Both devices must be available and unrelated to HA power/coordinator power.
5. Start A at **32-byte blocks (48 bytes allowed since 2026-11-17) and 1800 ms per-device response pacing**. Observe 5% progress with normal non-OTA network functionality. Start B at the same settings only when the original first transfer is healthy; do not start both on exactly the same second.
6. Observe both transfers for 15 minutes with separate timestamped transaction/offset histories. Compare aggregate throughput and unrelated Zigbee command latency against the single-transfer baseline. If no errors, *future test runs* can try 1500 ms per device, then 1200 ms; do not restart the active transfers just to change pacing.
7. After completion, confirm **each exact build and role from fresh live Zigbee ZDO/Basic evidence**, persistent relay/energy/policy, PM scaling/reporting when applicable, Router parent/child routing, connectivity, rejoin/bindings and normal real-world function. Release each slot only after its own recorded terminal evidence.

## Throughput and acceptance

Each OTA image of length L bytes, 32-byte chunks and delay D seconds has a **best-case response-pacing bound** `ceil(L/32) * D`, excluding image-query, retries, Zigbee air time and flash write stalls.

- One 32B / 1200ms stream offers ~0.83 image blocks/s.
- Two 32B / 1800ms streams offer ~1.11 blocks/s aggregate, a theoretical +33% offered block rate.
- Two 32B / 1500ms streams offer ~1.33 blocks/s; two 32B / 1200ms streams ~1.67 blocks/s.
- The actual criterion is **time to finish both** and **network stability**, not a single OTA percentage/ETA. Parallelism can be slower when UART/ZNP, APS retries, indirect queues or mesh routes saturate.

Stop admitting new sessions on any unrelated traffic/control regression, repeated transfer inactivity, `BUFFER_FULL`, ZNP/AF/ZDO admission failures, `SOURCE_ROUTE_FAILURE` bursts, coordinator resets, third unscheduled OTA, or invalid/unknown device state. Log first; do not blind-retry, power-cycle or globally restart while another transfer is still valid. Per-device abort is only a deliberate, separately evaluated recovery operation.

For the custom R4 coordinator, capture Z2M-visible `AF_DATA_REQUEST`/`BUFFER_FULL`/route and source-route failures, serial/SRSP latency, unexpected resets, and ordinary Zigbee application latencies. The R4 firmware has vendor diagnostic counters, but the official serial owner does not expose all internals via `bridge/info`; a reviewed same-owner diagnostic extension would be needed for full coordinator queue high-water measurements. Basic `bridge/info` revision 0 is **not** verification of the currently installed custom R4 revision.

## Software validation

New tests cover reciprocal two-slot allowlists, independent phase ownership, legacy/parallel mutual exclusion, third/duplicate rejection, invalid profile refusal, multithreaded admission races, and disabled automatic dual retry/rejoin. Run existing network ownership, OTA campaign and targeted Z2M tests. **No live OTA, coordinator flash, broker changes or Zigbee2MQTT restart was performed by the prototype.**


## Code-review findings and disposition (2026-11-14)

| Priority | Finding | Disposition |
| --- | --- | --- |
| Critical | Initial prototype serialized coordinator-slot acquisition and release but **not `update()`**. Concurrent metadata update could `os.replace()` a previously released lease, resurrecting coordinator ownership | **Fixed** by serializing update, acquire and release on the same atomic coordinator mutex; tested by 24 two-thread terminal-update/release races |
| High | The normal same-role supervisor may automatically reconcile a failed prior campaign using hard-coded `join_strategy="auto"`, ignoring parallel-profile no-rejoin policy | **Fixed**: dual supervisors refuse reuse of any existing per-device campaign work lock; peer-free manual recovery and a new workdir required |
| High | Second-slot admission could occur when the reciprocal first peer was only in preflight or already completed | **Fixed**: first peer must hold `ota_running` at the moment of second-slot admission; require live overlapping progress evidence to count actual concurrency |
| High | A direct runner could bypass pacing and no-rejoin rules if they were checked only in the supervisor, rather than the shared lease authority | **Fixed**: two-slot authority itself requires 32- or 48-byte blocks (`bseed_ota_block_policy.py`), 1800ms or slower pacing, no permit-join-all, no source-rejoin, same role, no FORCE |
| Medium | Legacy and dual paths could race to admit independent leases | **Fixed** by shared atomic directory admission mutex, tested with parallel workers and singleton/dual mutual exclusion |
| Open | Two devices sharing an overloaded mesh parent may degrade real-world control even if both OTA percentages rise | Check passive topology and baseline unrelated command latency, then monitor coordinator `BUFFER_FULL`, `SOURCE_ROUTE_FAILURE` and ZNP serial response timing |
| Open | A stale `ota_running` lease alone does not prove active device OTA-block traffic | At actual pilot launch use fresh per-IEEE Zigbee2MQTT progress and independent overlapping timestamps |
| Open | Entrance non-PM `r18 -> r19` lacks tested board-specific SWire readback/recovery evidence. Existing no-readback exception applies only to `v8 -> r18` | **Not authorized to flash** by this PR; independent acceptance needed |
| Open | Cabinet PM Client is responding to relay read (ON, 0 W), but one power-only read timed out | Fresh repeatable PM readings and attached-load checks are still required |
| Open | KitchenSocketRight `r19 -> r22` completed at 19:57:52 and independent postflash evidence now reports `postflash_candidate` with zero issues, but its singleton network lease remains held | Preserve old owner until separate evidence-backed network lease release; never rewrite an active singleton for this pilot |

### Test scope

The targeted network-ownership, runner, supervisor, OTA campaign and dual-slot tests exercise the race fixes; full coordinator hardware stress and route independence cannot be proven by offline tests. New dual profile files and sealed OTA data are stored only in private directories, not the repository or the public OTA index. No coordinator firmware, live OTA session or Zigbee2MQTT process was modified by this branch.
