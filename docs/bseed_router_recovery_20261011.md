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
  requests plus 60 minutes without positive inbound before recovery.
- Any incoming authenticated application ZCL or successful coordinator ZDO
  response resets the failure streak and records positive evidence.
- After at least **four failed probes** and **20 minutes without any positive
  inbound evidence**, request one secure ZDO rejoin using the same saved
  channel mask and security (no factory reset, no BDB network steering).
- The one-shot is protected by a 10-minute callback deadline and a 30-minute
  cooldown; ordinary SDK-owned `zb_rejoinReqWithBackOff` is never overlapped.
- During active OTA, both probes and rejoin are suppressed. If the SDK loses
  every OTA completion callback, OTA ownership expires after two hours rather
  than suppressing automatic recovery indefinitely. An OTA over two hours
  remains a specific hardware canary case.
- When a previously joined Telink Router is continuously NOT_JOINED for
  45 minutes plus an independent 0–15-minute mesh-wide stagger, it
  schedules **one per boot** controlled, metering-checkpointed
  software reboot per boot if not in OTA; network keys, binding state, IEEE
  address, Zigbee membership NVM and relay state are not deliberately cleared.
  This is a last resort, **not** the response to ordinary route errors.
- All roles re-arm Device Announce after observed unjoin/rejoin and retry
  rejected requests every 5s. Silicon Labs steering start failures clear
  their state and are retried at a bounded rate.

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
