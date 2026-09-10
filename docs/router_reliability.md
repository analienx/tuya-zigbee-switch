# Telink Router reliability

This fork treats a mains-powered Zigbee Router as network infrastructure, not just a relay that happens to route. Router firmware therefore has stricter recovery requirements than EndDevice firmware.

## Recovery model

Telink SDK 3.7.2.0 provides two distinct mechanisms:

- **BDB network steering** for joining/commissioning a factory-new device.
- **ZDO rejoin with backoff** for a device that already owns network state and temporarily loses connectivity.

Those operations must not run on top of each other. The common application loop periodically asks the HAL to restore connectivity when the device is neither joined nor already joining. On Telink, the historical `hal_zigbee_start_network_steering()` entry point is therefore recovery-aware:

1. If the device is already joined, no action is taken.
2. If steering or rejoin recovery is already active, no second operation is started.
3. A factory-new device starts BDB steering.
4. A previously joined device starts `zb_rejoinReqWithBackOff()` instead of fresh commissioning.
5. Both active steering and active rejoin are exposed to the common application as `HAL_ZIGBEE_NETWORK_JOINING`, preventing repeated application ticks from restarting BDB.
6. Once the Telink stack reports the device joined again, the application-side recovery state is cleared.

## Why this matters

Before this separation, a previously joined Telink Router could enter SDK rejoin/backoff while the application still reported it as `NOT_JOINED`. The next application tick could then call `bdb_networkSteerStart()` over that recovery. In Telink 3.7.2.0, BDB steering changes commissioning-mode state before the BDB busy check, so repeated calls during rejoin are an unsafe recovery pattern.

The firmware also previously grouped `BDB_COMMISSION_STA_NO_SCAN_RESPONSE` with `BDB_COMMISSION_STA_PARENT_LOST`. That could attempt rejoin recovery after a factory-new scan found no network. Those terminal conditions are now separated: a failed initial scan returns to the ordinary steering path, while parent loss and rejoin failure use the rejoin/backoff path.

## What this change does not do

- It does not increase child, neighbor, routing, APS or packet-buffer table sizes.
- It does not change radio power, channel, LQI thresholds or timing heuristics.
- It does not change BSEED GPIO mappings, BL0937 metering, relay/button behavior, OTA identity, flash layout or NVM ABI.
- It does not claim that every Router-unavailability report is caused by this state race.

The 8258 `16` child-table limit is intentionally left unchanged. The Router uses a constrained neighbor-table budget and there is not yet evidence that simply increasing child capacity would improve reliability.

## Validation contract

Before broad deployment, the Router candidate must pass all host tests, formatting/lint, real TC32 build/reproducibility gates and an already-custom Router canary. Live validation should check that a temporary network interruption produces one recovery sequence rather than repeated BDB steering, that the device rejoins without reset or physical input, and that normal switching, metering, `Mgmt_Lqi`, `Mgmt_Rtg` and child/router behavior remain healthy.
