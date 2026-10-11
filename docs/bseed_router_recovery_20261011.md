# Router recovery hardening — 2026-10-11 (draft canary)

**Status:** GitHub Actions / hardware acceptance required. No automatic deployment or
release promotion. Tracks [issue #71](https://github.com/analienx/tuya-zigbee-switch/issues/71)
and [draft PR #74](https://github.com/analienx/tuya-zigbee-switch/pull/74).

## Incident and diagnosis

BedroomSocketBalcony (non-PM `TS011F-BS`, IEEE `0xa4c138ac993cc245`)
ceased responding and recovered after a physical power cycle on 11 October.
Zigbee2MQTT previously reported NWK_NO_ROUTE and later repeated ZCL timeouts.
Those reports prove unavailable coordinator-to-device communication, **not**
whether the device still considered itself JOINED. A Router is not parented like
an End Device; blindly applying Client MAC-poll health logic is incorrect.

At source head before this change, Telink separated fresh BDB network steering
from `zb_rejoinReqWithBackOff()`. Nevertheless an SDK-owned rejoin can be
app-side latched indefinitely as JOINING; a Router that remains JOINED while its
coordinator path is broken bypasses ordinary rejoin detection completely.
Additionally the shared application treated failed Device Announce enqueuing as
success and did not re-arm Router announcements after a join-status transition.
Silicon Labs independently latched joining even if steering rejected the call
synchronously.

## Verified upstream API and implementation references

1. Telink `zb_api.h`: `zb_rejoinReq()`,
   `zb_rejoinReqWithBackOff()`, and `zb_zdoNodeDescReq()` apply to Routers.
   Backoff is configured through ZDO attributes, but the public header does
   not prove perpetual, unstoppable recovery.
   https://github.com/telink-semi/telink_zigbee_sdk/blob/master/tl_zigbee_sdk/stack/zigbee/zbapi/zb_api.h
2. Telink `bdb.c` uses a Node Descriptor request to coordinator `0x0000`
   as a genuine network-reachability check; its response callback inspects
   `zdo_node_descript_resp_t.status`.
   https://github.com/telink-semi/telink_zigbee_sdk/blob/master/tl_zigbee_sdk/stack/zigbee/bdb/bdb.c
3. Silicon Labs network-steering API returns synchronous errors. Merely
   marking `network_steering_in_progress=true` before calling it without
   inspecting return status can permanently block a retry.
   https://docs.silabs.com/zigbee/8.2.2/zigbee-af-api/network-steering
4. Silicon Labs Zigbee routing documentation distinguishes NWK route repair,
   neighbor loss and full join operations. A route error is not necessarily a
   network-membership failure.
   https://docs.silabs.com/zigbee/latest/zigbee-concepts-network/table-routing

## Implemented recovery model

All Telink Routers share `src/telink/hal/zigbee_network.c`; no per-board fork.

- Every 6 minutes plus a deterministic 0–120s IEEE-derived stagger, make at
  most one pending ZDO Node Descriptor request to coordinator `0x0000`.
  Accept only a complete response with SUCCESS and coordinator address.
  Time an unanswered request out after 30 seconds. Track queue or SDK
  request rejections separately: require at least eight consecutive rejected
  requests plus 60 minutes without a successful coordinator reply before
  recovery.
- Only a validated coordinator Node Descriptor response resets the
  coordinator-probe failure history. Neighbor-origin application ZCL updates
  **separate local-traffic evidence**; it must not make an unreachable trust
  center look reachable. Coordinator responses must match source 0x0000,
  request sequence, interest address and successful descriptor result.
- After at least **four unanswered probes** and **20 minutes without a
  successful coordinator response** (or **60 minutes if there is recent
  inbound neighbor traffic**), consider a secure ZDO rejoin using the saved
  channel mask and security (no factory reset or BDB steering). Actual
  rejoin is further staggered by 0–15 minutes. Eight consecutive immediate
  SDK ZDO request rejections require a stronger 60-minute coordinator-silence
  condition and the same per-router stagger.
- The one-shot owns Zigbee recovery even across a temporary NOT_JOINED
  transition; ordinary SDK-owned `zb_rejoinReqWithBackOff` cannot overlap.
  On a missing terminal callback at 10 minutes the one-shot becomes
  **orphaned**, blocking additional rejoin launches; a separate bounded
  software-rescue path is used if the node stays isolated.
- The one-shot has a 30-minute retry cooldown after normal completion.
  A continuously joined Router remains eligible for a *single* last-resort
  checkpointed software reboot only after **3 hours without coordinator
  success, at least 90 minutes without neighbor ZCL, and repeated confirmed
  probe failures and/or orphaned SDK recovery**. A 15-minute per-router
  reboot stagger prevents synchronized restarts.
- During an active OTA, probes, rejoin and rescue are suppressed. Two-hour
  callback lease expiry may clear stale ownership **only if the Telink SDK
  itself reports NORMAL OTA status**; it cannot interrupt a genuine active or
  paused transfer.
- When a previously joined Telink Router is continuously NOT_JOINED for
  45 minutes plus an independent 0–15-minute mesh-wide stagger, it
  schedules a **single** controlled, metering-checkpointed
  software reboot per boot if not in OTA; network keys, binding state, IEEE
  address, Zigbee membership NVM and relay state are not deliberately cleared.
  This is a last resort, **not** the response to ordinary route errors.
- All roles re-arm Device Announce after observed unjoin/rejoin and retry
  rejected enqueue requests every 5s. Both Telink and Silicon Labs have
  an explicit SDK-confirmed join-epoch hook, covering rejoin without an
  observable NOT_JOINED interval. Silicon Labs rejected or ambiguous
  steering completions are cooled down before retrying.
- The Router recovery watchdog is excluded from Telink coordinators. The
  existing coordinator `parentInfo`, always-on radio and power descriptors
  remain intact. Silicon Labs **joined-but-isolated** route recovery is
  now implemented as a separate native Zigbee stack path (below), but remains
  a release blocker until the genuine EFR32 SDK build and hardware canary pass.

## Best-practices alignment — independent 11 Oct review

Primary references: [Silicon Labs Zigbee Networking Concepts / route repair](https://docs.silabs.com/zigbee/8.2.3/zigbee-concepts/networking-concepts),
[Silicon Labs Zigbee Standard Security / rejoin](https://docs.silabs.com/zigbee/8.2.1/zigbee-security/03-standard-security),
[Silicon Labs Source Routing](https://docs.silabs.com/zigbee/8.2.0/zigbee-stack-api/source-route),
and pinned [Telink 3.7.2.0 SDK APIs](https://github.com/telink-semi/telink_zigbee_sdk/blob/master/tl_zigbee_sdk/stack/zigbee/zbapi/zb_api.h).

1. **Route discovery and repair before rejoin.** APS/NWK route discovery is the
   normal reaction when a unicast cannot reach its destination. The 6m ZDO
   coordinator request is a low-rate real bidirectional exchange using the
   stack's native routing mechanism and can refresh reachability. Four timed-out
   exchanges or a sustained ZDO queue failure are prerequisite evidence, not
   a one-off `NWK_NO_ROUTE` alarm. If the Router is **still servicing
   neighbors**, require **60 minutes coordinator silence** instead of 20.
   Add a **0–15-minute per-IEEE stagger to actual secure rejoin** (not just
   the ZDO probe or last-resort reboot). Both safeguards reduce churn during
   coordinator or RF outages on a mesh with 60+ Routers.
2. **Source-route asymmetry is an independent hazard.** A Router-to-coordinator
   ZDO request that gets a coordinator reply proves this exchange succeeded,
   but does **not** guarantee the coordinator's independent source-routed ZCL
   requests follow a healthy path. `NWK_NO_ROUTE` at Zigbee2MQTT may need
   coordinator-side route-record/MTORR refresh, source-route table sizing,
   routing/neighbor telemetry and coordinator health fixes. Hardware canary
   requires **two separate verified directions**: Router-originated ZDO
   request/response and coordinator-originated relay/read traffic.
3. **Separate membership, security and routing.** SDK JOINED state alone is
   insufficient; *one* failed health probe alone is also insufficient. A
   secure rejoin is attempted only after prolonged demonstrated failure;
   its original network credentials and IEEE identity are retained. Network
   permit-join is **not required** for a secure Zigbee rejoin; code never
   enables it. No unencrypted trust-center fallback, factory reset or PAN
   reformation is introduced.
4. **One SDK operation at a time.** Telink 3.7.2.0 has distinct
   `zb_rejoinReqWithBackOff` and one-shot `zb_rejoinReq` lifecycles. A
   nonterminal `BDB_COMMISSION_STA_PARENT_LOST` cannot release one-shot
   ownership or start backoff on top of it. Release ownership only on
   terminal success/failure. If callback is missing, keep an orphan guard
   and use a delayed non-destructive rescue rather than starting another scan.
5. **Active OTA is a stronger safety condition than a timer.** Read the SDK
   Image Upgrade Status, not merely our callback timer; pause recovery until
   it is NORMAL and any callback-owned lease expires. Check the actual state
   throughout an interrupted/resumed OTA.
6. **Fleet and coordinator protection.** Do not instrument the coordinator
   with application Router watchdog code. Preserve child routing, receiver
   power settings, relay/metering state, security counters and NVM. Keep
   checkpointed software reboot exceptionally rare and staggered. CI proofs
   do not establish child retention or live route repair.

## Gaps closed in subsequent code review — persistent budget and other stacks

### Flash-backed rescue budget

Telink Routers use `NV_ITEM_ROUTER_RESCUE_BUDGET=70`, an independent
4-byte application-NVM record with magic, complemented counter and checksum.
**No network security, relay, metering or existing configuration record is
rewritten by the budget.** Before **either** checkpointed rescue reboot,
the controller must successfully advance, re-read and verify this record.
At most **two self-rescue reboots across multiple power cycles** are allowed.
Unknown/corrupt read and write/readback failures fail closed (deny reboot).
The budget is restored to zero only after **12 hours of successful, unbroken
coordinator reachability and 24 positive ZDO responses**. Timeouts,
rejected ZDO sends, SDK rejoin intervals and OTA periods reset the health
window. This is not a real-time-clock-dependent rate limit; it intentionally
never resets merely because someone power cycled the socket. Expect at most
three 4-byte NVM writes per sustained outage/recovery cycle (two consumes and
one proven reset), not one per main-loop tick.

Host CI includes a compiled C fault-injection test simulating software boots,
NVM corruption, flash read/write denial and 12-hour health proof.

### Silicon Labs joined-but-isolated recovery

`src/silabs/hal/zigbee.c` now tracks coordinator Node Descriptor
request/replies independently of network JOINED, with a single pending
request, 30-second response deadline, 6-minute plus device-specific jitter,
four unsuccessful responses plus a quiet window and up to 60-minute delay
while other nodes still communicate. The request uses native
`SL_ZIGBEE_APS_OPTION_ENABLE_ROUTE_DISCOVERY`. An encrypted
`sl_zigbee_find_and_rejoin_network(true,...)` attempts only the *current*
channel and retains device type/network credentials. No insecure fallback,
permit-join, factory reset or automatic mass reboot exists. Stack UP/DOWN
callbacks guard in-flight rejoin ownership and cooldown; a missing callback
or OTA image-block transfer suppresses disruptive recovery. The post-rejoin
NOT_JOINED path continues with encrypted rejoin rather than BDB steering.
**No hardware deployment or acceptance is implied:** exact-head
EFR32MG21 compile and RF/OTA canary are mandatory.

### Coordinator source-route diagnosis

`helper_scripts/bseed_coordinator_route_triage.py` consumes a local
operator-supplied Zigbee2MQTT log extract and an exact IEEE/NWK identity
and emits **only sanitized read-only counters**. It distinguishes
coordinator->Router `NWK_NO_ROUTE`/source-route errors and actual inbound
traffic, identifies possible asymmetric routing, and keeps Z-Stack
and Ember-specific remediation distinct. For example:

```sh
python helper_scripts/bseed_coordinator_route_triage.py \\
  --log /path/to/read-only-z2m-extract.log \\
  --ieee 0xa4c138ac993cc245 --nwk 0x69D8 --adapter zstack
```

Never apply Ember `stack_config.json` concentrator settings or an
Ember MTORR API to Z-Stack hardware. The diagnostic tool does not
send MQTT requests, networkmap scans, resets, firmware updates or commands.
An operator must separately validate **coordinator-originated ZCL read**
and **Router-originated ZDO/ZCL traffic** before approving any targeted
coordinator repair. Source-route table management is coordinator-specific,
and one device's successful outgoing ZDO probe does not prove the
coordinator's own outgoing source route is healthy.

**Explicit remaining limitations:** The Telink main loop already starts
the hardware watchdog via `drv_wd_setInterval(1000)` and `drv_wd_start()`,
clearing it around the stack/application loop. A total MCU stall should
therefore invoke the **existing** hardware watchdog, not the new Router
rejoin logic. A Zigbee stack that remains nominally runnable but can no longer
exchange network traffic continues to feed that watchdog and requires the
application's independent bounded connectivity checks; those checks cannot
recover a permanently broken radio peripheral or physical RF outage.
The last-resort `schedule_reboot` requires the superloop to keep running.
The reboot budget is now persistent, fail-closed and independently
tested (see above), but the firmware still requires one-device hardware proof
that the NVM item survives OTA, reset and genuine coordinator outages. Silicon Labs
JOINED-but-isolated recovery is not yet implemented. Coordinator-source-route
repair requires changes outside this device firmware and separate acceptance.

## Candidate identities (not published)

| Hardware | Router candidate | Image type | File version |
|---|---|---:|---|
| TS011F non-PM | `1.1.3-bseedr13` | 43555 | `0x11023018` |
| TS011F PM | `1.2.5-bseedr14` | 43556 | `0x1205301C` |
| TS0726-3-BS | `1.1.9-bseedrv1` | 45577 | `0x11023010` |

The shared application changed, so non-PM Client `1.1.3-bseedc11`
(`0x11023019`, type 65026) and PM Client `1.2.5-bseedcli17`
(`0x1205301C`, type 65024) are recompiled separately. Previous sealed
firmware binaries and OTA indices remain untouched.

## Offline and hardware acceptance gates

**Offline:** exact candidate SHA on GitHub-hosted Actions only; test/lint,
native C policy threshold and 32-bit timer-wrap tests, join/rejoin callback
fault injection, no-overlap/role/preprocessor contract guards,
pinned TC32 builds for PM and non-PM; both PM and non-PM Router/Client role
matrices; isolated TS0726 Router recovery artifact; OTA embedded Basic identity,
header, deterministic hashes, canonical release allocation, no old tuple relabel
and no OTA-index mutation.

**Hardware:** authorized *unloaded* single Router canary, verified exact
firmware identity post-flash (not 100% transfer), relay and power-monitor
preservation, child routing, no bursty device announcements, coordinator
unicast reachability through genuine RF interruption and restoration **without
power cycling**; separate route-only failure while still joined, genuine
SDK NOT_JOINED loss, 30-minute recovery cooldown, OTA pause/resume, an outage
of the coordinator longer than a probe cycle, recovery without permitting new
joins, no factory reset or NVM rollback, stale ZDO response handling, and
stagger effects when multiple devices are affected.

**Important:** The SDK 3.7.2.0 behavior of one-shot Router rejoin from a
JOINED-but-stranded state is an unproven hardware assumption. The code uses
the official Router API, but compiler and source-level validation cannot
replace hardware proof. Do not promote this PR as a fleet fix until that
canary passes, and never perform OTA under a stale campaign lease.
