# Parent and route link policy (all Telink Routers and mains Clients)

Decision logic: `src/hal/link_policy.h` (pure C, natively tested on GitHub
Actions by `tests/test_bseed_link_policy_runtime.py`). Actions:
`src/telink/hal/zigbee_network.c`. Enabled for every Telink build by
`BSEED_LINK_POLICY ?= 1` in `src/telink/Makefile`.

## Why

KitchenSocketLeft (PM Client cli20, 2026-11-02) reported over 8.1 h of
uptime (no reboot): 40 parent losses, 95 rejoin starts, 54 failures, 41
successes, last received LQI 51 / RSSI -82 dBm. The network map showed its
parent was a stock Tuya plug (KitchenSocketOven, `_TZ3210_6przvg2s` 1.1.6),
while the in-house PM Router beside it did not list it at all. The Telink
3.7.2.0 SDK defaults `PRE_PARENT_FIRST_WHEN_REJOIN = TRUE`
(`zigbee/common/zb_config.c`, read by `nwk_join.o` in `libzb_ed.a` and
`libzb_router.a`), so every rejoin went back to the same weak parent.

## Principles

1. Stay with the current parent/route by default; move only on repeated,
   measured failure.
2. Least disruptive action first: observe, route refresh, rescan on rejoin,
   rejoin, then (Routers only) the existing budgeted reboot.
3. Hysteresis: trigger count within a window, cooldowns, return to sticky.
4. SDK path costs (1 best .. 7 worst), never raw LQI. On TLSR8258,
   LQI = 255 * (RSSI + 99) / 84 and LQI <= 45 (about -84 dBm) is already cost 7.
5. Never act while an OTA, SDK-owned rejoin backoff or Router one-shot owns
   the network.

## Clients (mains, Rx-on)

| State | Rule |
|---|---|
| Sticky (default) | `PRE_PARENT_FIRST_WHEN_REJOIN = TRUE` |
| Enter rescan | 3 parent-loss signals within 60 min (BDB `PARENT_LOST` or silent MAC-poll recovery) |
| Rescan | Flag set `FALSE` immediately before our `zb_rejoinReqWithBackOff()`; the SDK then scans and chooses the best parent |
| Back to sticky | 6 h without a parent loss |

## Routers

| Step | Trigger | Action |
|---|---|---|
| Observe | every 60 s | next hop to 0x0000 from `g_routingTab` (or the coordinator as a direct neighbour), its neighbour-table cost, best other router neighbour cost |
| Proactive refresh | next hop at cost 7 for 15 min and another router neighbour at cost <= 3 | `zb_routeDiscReq()` unicast (`ZB_ADDR_16BIT_DEV_OR_BROADCAST`) to 0x0000 |
| Reactive refresh | 2 failed coordinator probes | same |
| Refresh cooldown | 60 min after any request | |
| Rejoin | existing threshold (4 probe failures, 20 min) | existing secure one-shot with `PRE_PARENT_FIRST_WHEN_REJOIN = FALSE`; restored to `TRUE` on its terminal callback |
| Reboot | existing rules | unchanged |

Address mode 0 (ZDP `SHORT_ADDR_MODE`) must never be used for route
discovery: in `nlme_routeDisc_req_t` it means a broadcast many-to-one
discovery that would make the Router a concentrator.

The coordinator is TI Z-Stack, which normally issues periodic many-to-one
route requests. A unicast rediscovery toward it may be redundant; the 0xFF13
counters decide whether the proactive step is kept.

## Basic 0xFF13 link snapshot

ZCL octet string, length byte 16, then (multibyte little-endian):

| Offset | Meaning |
|---|---|
| 0 | schema (1) |
| 1 | flags: bit0 Router, bit1 Client rescan mode, bit2 link known |
| 2-3 | Client parent / Router next hop to 0x0000 (0xFFFF unknown) |
| 4 | its LQI |
| 5 | its SDK cost |
| 6 | Client parent losses in the last hour / Router failed coordinator probes |
| 7 | Client rescan-mode entries / Router route refreshes (saturating) |
| 8 | Router rescan rejoins (saturating) |
| 9-12 | ms since last policy action, 0xFFFFFFFF if none |
| 13 | Router best other neighbour cost |
| 14-15 | reserved |

Read each Basic diagnostic attribute separately (0xFF10, 0xFF12, 0xFF13):
a combined read of the 48-byte snapshots times out on a Client.

## Identities

| Line | Router | Client | Version |
|---|---|---|---|
| PM TS011F-BS-PM | 1.2.5-bseedr18 | 1.2.5-bseedcli21 | 0x12053020 |
| Non-PM TS011F-BS | 1.1.3-bseedr17 | 1.1.3-bseedc15 | 0x1102301C |
| TS0726-3-BS | 1.1.9-bseedrv2 (`link-policy-candidate`) | 1.1.8-bseedcli4 | 0x11023011 |

Non-BSEED boards (for example AVATTO TS0001/TS0002) build with the policy by
default; their release identities are handled by their own build path.

## Acceptance

Same-role canary on one device per line. KitchenSocketLeft first: pass when
it leaves the stock parent or parent losses fall from about 5/h to under
1/day over a 24 h soak, read from 0xFF10 and 0xFF13. A green CI never
authorizes flashing.
