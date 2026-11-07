# Parent and route link policy (all Telink Routers and mains Clients)

Decision logic: `src/hal/link_policy.h` (pure C, natively tested on GitHub
Actions by `tests/test_bseed_link_policy_runtime.py`). Actions:
`src/telink/hal/zigbee_network.c`. Enabled for every Telink build by
`BSEED_LINK_POLICY ?= 1` in `src/telink/Makefile`.

PR #114 adds the Client signal floor and weak-previous-parent restriction below.
Its current unsealed identities and release limits are in
[the recovery implementation notes](bseed_recovery_pr_a_design.md). The sealed
identity tables later in this document describe the earlier release only.

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
4. SDK path costs (1 best .. 7 worst) drive route selection. Client admission
   separately uses a bounded raw-LQI floor. On TLSR8258,
   LQI = 255 * (RSSI + 99) / 84 and LQI <= 45 (about -84 dBm) is already cost 7.
5. Never act while an OTA, SDK-owned rejoin backoff or Router one-shot owns
   the network.

## Clients (mains, Rx-on)

| State | Rule |
|---|---|
| Prefer previous parent | Only when its SDK cost is known and <=2 and the repeated-loss policy permits it. Unknown or poor links are not preferred. |
| Client admission floor | LQI >=118 for the first 30 s of backoff, >=94 until 90 s, then default 69. Success, rejection, failure or leave restores 69 early; OTA restores on the next superloop. Router admission is unchanged. |
| Enter rescan | 3 parent-loss signals within 60 min: a BDB `PARENT_LOST` that starts a new recovery, or silent-parent recovery. `PARENT_LOST` raised inside the SDK's own ongoing backoff is the same outage and is not counted. |
| Rescan | Flag set `FALSE` immediately before our `zb_rejoinReqWithBackOff()`; the SDK then scans and chooses the best parent |
| Back to sticky | 6 h without a parent loss, evaluated before a new loss is recorded (recording first would reset the timer and never leave rescan mode) |

## Routers

| Step | Trigger | Action |
|---|---|---|
| Observe | every 60 s | next hop to 0x0000: the coordinator itself when it is in the neighbour table (NWK forwards to neighbours directly), else the active `g_routingTab` entry; its cost; best other router neighbour cost. Not while an orphaned one-shot may own the radio. |
| Proactive refresh | next hop at cost 7 for 15 min and another router neighbour at cost <= 3 | `zb_routeDiscReq()` unicast (`ZB_ADDR_16BIT_DEV_OR_BROADCAST`) to 0x0000 |
| Reactive refresh | 2 failed coordinator probes | same |
| Refresh cooldown | 60 min after any request; no rejoin scan for 2 min after a request | |
| Rejoin | existing threshold (4 probe failures, 20 min) | existing secure one-shot with `PRE_PARENT_FIRST_WHEN_REJOIN = FALSE`; restored to `TRUE` on rejection, on its terminal callback and on both orphaned-callback paths. Counted only when the SDK accepted the request. |
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
| 8 | Router rescan rejoins (saturating); Client active LQI admission floor (69/94/118) |
| 9-12 | ms since last policy action, 0xFFFFFFFF if none |
| 13 | Router best other neighbour cost |
| 14-15 | last controlled rescue reason and SDK recovery state at checkpoint |

Read each Basic diagnostic attribute separately (0xFF10, 0xFF12, 0xFF13):
a combined read of the 48-byte snapshots times out on a Client.

## Identities

| Line | Router | Client | Version |
|---|---|---|---|
| PM TS011F-BS-PM | 1.2.5-bseedr19 | 1.2.5-bseedcli22 | 0x12053021 |
| Non-PM TS011F-BS | 1.1.3-bseedr18 | 1.1.3-bseedc16 | 0x1102301D |
| TS0726-3-BS | 1.1.9-bseedrv2 (`link-policy-candidate`) | 1.1.8-bseedcli4 | 0x11023011 |

TS0726 rv2/cli4 are reserved in `bseed_identity.json` (no hash yet). Historical
identity builds (TS0726 v8/lv3/rv1, PM v8u4 and its rc channels) pass
`BSEED_LINK_POLICY=0` so their bytes do not drift further. Non-BSEED boards
(for example AVATTO TS0001/TS0002) build with the policy by default; their
release identities are handled by their own build path.

## Acceptance

Same-role canary on one device per line. KitchenSocketLeft first: pass when
it leaves the stock parent or parent losses fall from about 5/h to under
1/day over a 24 h soak, read from 0xFF10 and 0xFF13. A green CI never
authorizes flashing.

### Earlier release sealed from exact-SHA public CI

Source `1cc08a92d4dd68ff3607dc224f707c3e757d4ec8`. All required GitHub-hosted checks passed on it
([candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449369099),
[test](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368782), [PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368902),
[non-PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368974),
[TC32 Router](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368806)). The runner's registry
proposal (SHA-256 `258c8171f4c0357da44dddbf3717624191256df91bc703e8a49b41531b384edc`) was committed unchanged. It
adds these four entries and alters no earlier one.

| Native forward OTA | SHA-256 |
| --- | --- |
| PM Router r19 | `d17571a1d996ff798f1b6334cac1d799fbc1c6125a0212741aca89446053fc95` |
| PM Client cli22 | `a8d2e38f53c63a4f0561076c1c561a1917f25ece602b16d256ff1b1c36a7bbf8` |
| Non-PM Router r18 | `4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9` |
| Non-PM Client c16 | `d4057825661eb4a6c943ca4f8a5b02d7c709acdb960e4102fd5a185e4f87e321` |

### Retired seal (superseded before deployment)

The first seal below was superseded by the code-review fixes before any
device received it; its four tuples are `retired` in the registry and must
never be deployed. The candidates above replace them.


Source `d06e130cf63fc4e85a2fd6c03648bbc08aef942b`. All required GitHub-hosted
checks passed on it ([candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41437647854),
[test](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41437647874),
[PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41437647734),
[non-PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41437647867),
[TC32 Router](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41437647817)).
The runner's registry proposal (SHA-256
`a616b5dba0fa2c94ef51faf046ad26a946b35bb9106fc82070cebc8e4a724deb`) was
committed unchanged. It adds these four entries and alters no earlier one.
Native integrity and embedded Basic build IDs were verified by the runner.

| Native forward OTA | SHA-256 |
| --- | --- |
| PM Router r18 | `ce4ec3f1fcd9e610058cc8f5b4a00b5f6673b367a6d2e36b095d847ccc0df8cc` |
| PM Client cli21 | `5f0389c62629285c368f0b69a9c745d83d914a3e52df5261d0c325451aee1b48` |
| Non-PM Router r17 | `0643ad1193ad9d9efa08a8f604fc7ad39e5d935a49acd1350c2515b00d6e9ab8` |
| Non-PM Client c15 | `1b844266a17b098c9c6e3dd0984c3813eefd65f5d45727f8c3e25aee639bade7` |

TS0726 rv2/cli4 are built and checked by their own workflows and are not part
of this registry proposal. Hardware acceptance pending for all lines.
