# BSEED four-variant firmware review — 2026-10-31

Source review of the PM and non-PM TS011F Router and mains Client firmware on
`main` (`4f477f87`), plus the newer, unmerged Router-family recovery work from
draft [PR #74](https://github.com/analienx/tuya-zigbee-switch/pull/74). This
records code and offline-test changes only. No device was flashed, probed or
reconfigured, and nothing here is hardware acceptance.

## Router-family recovery merged onto main

PR #74 was based on 2026-10-11, before PR #84 replaced the Client one-shot
recovery with SDK-owned backoff. It is merged here with main's Client design
kept: the Client probe hooks from the older base are dropped, and inbound ZCL
again only feeds the Router's neighbor-activity evidence.

It addresses issue #71 F1 and F2 for Telink Routers: a coordinator Node
Descriptor probe, a bounded secure one-shot rejoin, a budgeted (two per healthy
12 h) checkpointed rescue reboot for a Router stuck unjoined or isolated, and a
Device Announce re-armed per join epoch with retry on local send failure.

Review fix: a lost one-shot completion set `router_one_shot_stalled`, which was
cleared only by a terminal BDB callback. A later validated coordinator reply
reset the failure history but not the marker, so no further one-shot could
start for the rest of the uptime. A joined, validated reply now releases it.

## Fixes

| Area | Variants | Change |
| --- | --- | --- |
| Controlled reboot | all (PM effect) | OTA apply, reboot and network reset deferred forever while the metering checkpoint failed; a PM energy-record fault therefore blocked OTA apply and user resets. Deferral is now bounded to 12 × 5 s, then the reboot proceeds and the unsaved interval is lost. |
| Client role change | PM/non-PM Client | Reset or marker-write failure rebooted every boot without bound. Both now use the existing three-attempt in-place retry and then leave startup blocked. |
| Router role marker | PM/non-PM Router | A read error rewrote the marker as Router without a reset, erasing a pending Client→Router transition. Only NOT_FOUND initializes it now. |
| Overload lockout | PM Router/Client | Reconnects only accumulated, so unrelated overloads across weeks locked the relay out. One energized hour under every limit now forgives earlier reconnects. |
| Calibration | PM Router/Client | Network-written multipliers were unbounded and could make overloads read as ~0, disabling the hard trip. PM accepts 1/4×–4× of the verified factors; the string parser saturates. |

Each change has an executable host test with fault injection. The Router role
check still runs after `parse_config()`; moving it earlier would change the
hardware-verified Client→Router path and is not part of this change.

## Not changed (owner decision)

- Overload auto-reconnect follows the relay power-on setting, including after
  hard-limit trips, with a 5 s minimum delay.
- Protection is inactive while the meter sample is invalid (sampler stall).
- With power-on OFF, a trip clears only on reboot; there is no remote reset.

## Candidate identities

| Board | Router | Client | Native version |
| --- | --- | --- | --- |
| PM TS011F-BS-PM | `1.2.5-bseedr17`, type 43556 | `1.2.5-bseedcli20`, type 65024 | `0x1205301f` |
| Non-PM TS011F-BS | `1.1.3-bseedr16`, type 43555 | `1.1.3-bseedc14`, type 65026 | `0x1102301b` |

Build date `20261031`. The allocation test checks these against the canonical
allocator on GitHub-hosted CI.

### Sealed from exact-SHA public CI

Source `8636ee47671b139932aa3a3c864ca66165a6875f`. All required GitHub-hosted
checks passed on it ([candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41228766871),
[test](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41228766885),
[PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41228766886),
[non-PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41228766910),
[TC32 Router](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41228766893)).
The runner's registry proposal (SHA-256
`ef67e146bce97909e1b6e49e7649920f5f1a6138f0c4edf061615ca507d49b7f`) was
committed unchanged. It adds these four entries and alters no earlier one.
Native integrity and embedded Basic build IDs were verified by the runner.

| Native forward OTA | SHA-256 |
| --- | --- |
| PM Router r17 | `fb83b200fd9dafcb6d8181b90e2d7185a9bb3f27661fc4c0a2024bf2620dfa8c` |
| PM Client cli20 | `0a3da60605ed461d5627315850b6324c6badd2968275519a745c973ca65da929` |
| Non-PM Router r16 | `dafe1e058985488dee13a5c809f394897abe70d3e86cd808113a92cfa1f7cdd1` |
| Non-PM Client c14 | `90997e21eea73e0294bf89fba5cbc8db724e9cb9fc54e6f76cc3d61486ccce4a` |

The Zigbee2MQTT converter does not yet recognize these builds; add them in a
follow-up, as was done for r16/cli19 (#88), before any PM canary.

TS0726 Router builds share the Telink HAL and also receive the Router recovery
changes; they keep their separate version line.

## Remaining gates

Exact-SHA public CI and native builds, sealing from runner artifacts, an
independent review, and per-variant hardware canaries. In particular, the
Router rescue and one-shot paths have only been exercised against SDK fakes.
