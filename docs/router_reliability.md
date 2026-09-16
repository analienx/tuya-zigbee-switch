# Telink Router reliability

This fork treats a mains-powered Zigbee Router as network infrastructure, not just a relay that happens to route. Router firmware therefore has stricter recovery and child-parenting requirements than EndDevice firmware.

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

## Sleepy end-device parenting

Router reliability also includes being a correct parent for battery-powered sleepy children. Telink SDK 3.7.2.0 exposes keepalive capability bits, but its default Router NIB initializes `parentInfo` to zero. The BSEED Router target now advertises `MAC_DATA_POLL_KEEPALIVE_BIT` after Zigbee stack initialization, matching the MAC Data Poll keepalive mechanism used by sleepy end devices without claiming an unproven second keepalive mode.

The fix intentionally does **not** increase the Telink child table. The TLSR8258 Router has a constrained shared neighbor/child budget; advertising more child capacity without evidence could reduce routing headroom. The physical child-table limit remains 16.

### Hardware validation

The BSEED TS011F PM Router running `1.2.5-bseedv8u4` / `0x12053007` was validated with a real IKEA RODRET E2201:

- permit-join was targeted through the BSEED router;
- RODRET joined and completed interview/configuration cleanly;
- `on`, `off`, `brightness_move_up`, `brightness_move_down`, and `brightness_stop` actions were received;
- after an idle/sleep interval, the first wake action was delivered immediately;
- a routes-disabled LQI topology scan confirmed the RODRET as an actual child of the BSEED router with RX-off-when-idle;
- the BSEED canary preserved IEEE/NWK identity, relay behavior, BL0937 reporting and canonical configuration through the firmware update.

This closes the previous failure mode where an IKEA sleepy device could interview through a BSEED router but later configuration/action delivery was unreliable.

## Why recovery separation matters

Before this separation, a previously joined Telink Router could enter SDK rejoin/backoff while the application still reported it as `NOT_JOINED`. The next application tick could then call `bdb_networkSteerStart()` over that recovery. In Telink 3.7.2.0, BDB steering changes commissioning-mode state before the BDB busy check, so repeated calls during rejoin are an unsafe recovery pattern.

The firmware also previously grouped `BDB_COMMISSION_STA_NO_SCAN_RESPONSE` with `BDB_COMMISSION_STA_PARENT_LOST`. That could attempt rejoin recovery after a factory-new scan found no network. Those terminal conditions are now separated: a failed initial scan returns to the ordinary steering path, while parent loss and rejoin failure use the rejoin/backoff path.

## What these changes do not do

- They do not increase child, neighbor, routing, APS or packet-buffer table sizes.
- They do not change radio power, channel, LQI thresholds or timing heuristics.
- They do not change BSEED GPIO mappings, BL0937 metering, relay/button behavior, flash layout or NVM ABI.
- They do not claim that every Router-unavailability or mesh route error is caused by the Telink recovery state race.

The 8258 `16` child-table limit is intentionally left unchanged. The Router uses a constrained neighbor-table budget and there is no evidence that simply increasing child capacity would improve reliability.

## Validation contract

Router-relevant changes must pass host/static tests, formatting/lint, the real pinned-TC32 compile gate and exact-target hardware validation before broad release. Hardware checks cover network identity preservation, switching/metering, `Mgmt_Lqi`, `Mgmt_Rtg`, recovery behavior, and—when child-parenting code changes—real sleepy-end-device join/interview/actions/sleep-wake behavior plus topology confirmation.

The v8u4 BSEED PM canary has passed that contract with the IKEA RODRET E2201 test described above. Future Router changes must preserve the same boundary rather than treating a successful compile or interview alone as sufficient evidence.