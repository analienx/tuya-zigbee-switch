# BSEED socket firmware: golden images and role rollout


## OTA tooling finalization — 2026-10-10

The four sealed candidate identities below are unchanged. PM/non-PM Router
and mains-Client OTA recovery now share exact-source and candidate authority,
progress-gated retries, and per-device time diagnostics. Fresh ZCL power idle
proof applies only to PM hardware; non-PM uses its own physical-unloaded and
link-evidence gates. **Three minutes is not a validated default:** documented
Bedroom non-PM recovery required a longer request wait, while Kitchen PM and
Hifi experienced other failure modes. See
[bseed_ota_timeout_evidence_policy_20261010.md](bseed_ota_timeout_evidence_policy_20261010.md).
Firmware binaries are still sealed offline candidates; individual physical
role/relay/meter/routing acceptance must not be inferred from CI or OTA status.

## Current status — 2026-10-04

The September table below is retained as historical rollout context. The current sealed four-variant hardening set is newer:

| Socket board | Router candidate | Client candidate | Current hardware evidence |
|---|---|---|---|
| TS011F-BS-PM (`b28wrpvx`) | `1.2.5-bseedr12` / `0x12053019` / type `43556` | `1.2.5-bseedcli14` / `0x12053019` / type `65024` | Sealed/reproducible; broad per-device acceptance still required |
| TS011F-BS non-PM (`o1jzcxou`) | `1.1.3-bseedr12` / `0x11023016` / type `43555` | `1.1.3-bseedc9` / `0x11023016` / type `65026` | c9 live install/rejoin proven on `BedroomSocketCabinetRight`; full acceptance bookkeeping still pending |

The c8/c9 generation includes shared Telink clock, scheduler, network-recovery and diagnostics hardening. Client keepalive enforcement now stops overriding SDK disconnected recovery polling; rejoin/backoff is kept distinct from fresh steering; rejected starts are paced. Read-only Basic diagnostics `0xFF10` and `0xFF12` expose parent/rejoin, polling, stack latency, flash-service, frame-counter and radio evidence.

The c9 canary also showed that interrupted OTA state is retained across retries: transfers resumed from approximately 16% and 46% before the final successful apply. This does **not** mean c9 added a new direct OTA-abort algorithm: relevant deferred re-query logic predates c9. Treat the improvement as evidence for the broader timing/scheduler/network-recovery hardening, not as proof that every OTA abort root cause is eliminated.

After the successful c9 transfer, the socket reported `1.1.3-bseedc9`, live `EndDevice` ZDO role, fresh MQTT state and healthy connectivity without a power cycle. The earlier power-cycle requirement occurred while recovering the old c7 firmware from interrupted/stuck OTA/network state.

### Fleet rollout policy from this point

Custom BSEED sockets may be advanced serially to the latest sealed firmware for their **existing board and role**, one OTA at a time. Each device must preserve exact IEEE/board/role/image identity, pass fresh link and relay/load preflight, own the network OTA lock exclusively, and complete post-flash build/role/connectivity checks before the next socket starts. Stock TS011F devices are not part of this same-role custom-firmware rollout. Cross-role Router↔Client conversion remains a separate operation with rejoin/binding implications.


Status updated on 2026-09-25. **Router goldens are released; Mains Client remains a canary and must not be advertised as a normal OTA update.** The word *golden* here means an exact, retained, hardware-accepted image for a specific board and role—not a guarantee of perfect reliability.

| Socket board | Released Router | Router OTA image type | Client candidate | Client OTA image type | Client hardware status |
|---|---|---:|---|---:|---|
| TS011F-BS-PM (`b28wrpvx`) | `1.2.5-bseedv8u4`, `0x12053007` | `43556` | `1.2.5-bseedcli8`, `0x12053010` | `65024` | **Golden candidate, built 2026-09-25 (PM matrix, clean tree)**; shares board-wide FILEVER with Router rc3 `0x12053010`; spare-canary hardware acceptance pending |
| TS011F-BS non-PM (`o1jzcxou`) | `1.1.3-bseedv8`, `0x11023001` | `43555` | `1.1.2-bseedcli5-rc2`, `0x11023012` | `65026` | **Canary passed 2026-09-25 on BedroomSocketCabinetRight** (100% paced OTA, upgradeEnd, running fileVersion + swBuildId verified on the wire); release acceptance pending owner |

The PM Router `v8u4` hardware canary passed a real IKEA RODRET child join/interview, sleepy-wake action and topology test; see [router reliability](router_reliability.md). The non-PM Router release was hardware accepted and merged in PR #27. PR #31 (Client OTA-abort recovery) and PR #28 (byte-identical non-PM Client→golden-Router recovery image) were merged on 2026-09-19. Their merge does **not** constitute a passing Client hardware acceptance test.

## Current non-PM Client canary evidence

`BedroomSocketCabinetRight` has reported `1.1.2-bseedcli4`, role `EndDevice`, the canonical non-PM configuration, and relay `OFF` after the Router→Client OTA. A read-only Zigbee2MQTT/MQTT probe on 2026-09-19 succeeded on 19 of 20 relay reads: one seven-second timeout, then one approximately 2.94-second read; other successful replies were generally under 160 ms. This **fails the repeatability gate** despite successful installation and ON/OFF checks in the earlier transition campaign. Keep the existing canary under observation and do not convert additional sockets based on this run.

## Promotion gates, in order

1. Keep both exact Router goldens and their stock→Router wrappers available in `index_bseed.json`; verify file hashes and OTA headers against the checked-in binary artifacts. Never substitute a freshly rebuilt rollback payload for the hardware-proven Router image.
2. Diagnose the non-PM Client timeout with simultaneous Zigbee2MQTT/coordinator logs and a device-specific read probe. Separate MQTT-message loss, route failure, commissioning/rejoin activity and device application stalls; do not assign a cause from one timeout alone.
3. Repeat functional and recovery tests in a quiet network window, including canonical configuration, relay ON/OFF with a known safe load, consecutive relay reads, normal same-role OTA, interrupted OTA recovery, controlled disconnect/rejoin and preservation of IEEE/NVM. Record counts, timeouts, coordinator logs, build ID and role after the device has settled. **Any unexplained failure keeps the candidate unpromoted.**
4. Run the PM Client campaign on a *designated spare PM canary*, not the only working routing/measurement reference. Verify real BL0937 voltage/current/power/energy behavior with a safe known load and at no load, protections, relay, role, recovery and Client→golden-Router rollback. Also verify no regression in the remaining Router PM canary's sleepy-child behavior.
5. Publish native Client images in the normal index **only after both board-specific canaries pass**, with distinct Client image types and same-role updates. Publish Router→Client and Client→Router transitions in separate temporary indexes. The standard index must not advertise cross-role transitions.
6. Convert only a documented subset of sockets after confirming that the remaining Routers still provide adequate mesh coverage, sleepy-child parenting and redundancy. Record IEEE, board variant, intended role, installed build, rollback artifact hash and post-transition interview/bindings for each selected socket.

## Rollout safeguards

The Mains Client is an always-awake, mains-powered EndDevice; it is **not** a sleepy battery profile. Zigbee role is a compile-time stack choice, not a Zigbee2MQTT toggle. A role change resets Zigbee network state and bindings; reopen permit-join for the selected device, verify the rejoin and restore any direct bindings. Never leave a temporary cross-role OTA index active for routine updates. Keep a Router device in each area needed for coverage, and avoid concurrent OTAs, coordinator changes and Zigbee2MQTT restarts during a hardware canary. See [role-change procedure](bseed_roles.md).

`tests/test_bseed_golden_role_distribution.py` enforces the current fail-closed release index and checks that the published Router OTA files match their SHA-512, header identities and stock-conversion payloads. Revise this gate only alongside documented hardware acceptance evidence when Clients are promoted.

## Zigbee2MQTT reconnect relay read correction (integration candidate)

Zigbee2MQTT 2.14.0 `lib/extension/availability.ts` invokes the `state` converter on `device.endpoint()` after an announce; for BSEED TS011F sockets this defaults to switch endpoint 1, which exposes an **output** `genOnOff` cluster, not the relay's readable `onOff` attribute. The relay's input `genOnOff` lives on endpoint 2. The resulting `UNSUPPORTED_ATTRIBUTE` is not evidence that the relay failed to switch.

The generated PM and non-PM converter definitions now use a board-scoped wrapper around `onOff({endpointNames: ["relay"]})` that routes `convertGet` to endpoint 2 using the Zigbee2MQTT-provided `meta.device`. Normal `convertSet`, reports, exposure names and unrelated devices stay unchanged. The fix must be deployed to the live Zigbee2MQTT converter and checked against actual device-announcement logs before claiming this particular error resolved. It does **not** establish that the intermittent multi-second read delays or dropped reads have been fixed; those require separate mesh/firmware comparison and repeated canary acceptance.
