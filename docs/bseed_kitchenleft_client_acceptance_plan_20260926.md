# KitchenSocketLeft: upgrade before Client acceptance testing

## Decision

Update 2026-09-27: the owner requested a consolidated candidate before testing.
Use [cli11's release record](bseed_pm_consolidated_cli11_20260927.md) for the
current artifact/CI/sealing status. The cli10 identity and hashes below are
historical comparison evidence, not the next recommended image. cli11 includes
the missing hardening described below plus polling retry/rejoin corrections.
Complete the same acceptance sequence on cli11 after its offline gates pass.

Perform only the short identification, backup and OTA-readiness baseline on
installed cli6. Upgrade directly to the verified PM Client candidate before
spending time on metering, recovery and sustained-downlink acceptance. There
is no demonstrated requirement to install cli7, cli8 or cli9 in sequence.

The previously verified candidate was `1.2.5-bseedcli10` (16 ASCII bytes), native
and outer version `0x12053012` / 302329874, manufacturer 4417, image type 65024.
Use the PM Client `forward.ota`, not a Router return or stock-facing wrapper.
This is a canary recommendation, not evidence of completed hardware acceptance.

Verified downloaded artifact:

- Source: `6e87b05da201e0cdb0a99d8b89234a973d8c55cf`.
- SHA-256: `01cc6b30dcedc7f7d6b52cffc0f903da8c8027ea25417fdd43f45ddaa441fb8a`.
- SHA-512 matches the sealed identity registry.
- Later unchanged firmware rebuilds passed the [PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/36257771163)
  on `cd46953dd37210bf0b01f61977e92be2a0e39a57`.

Router rc5/rc6's overlength Basic string does not affect cli10: its string fits
the 16-byte limit exactly. The higher type-65024 version `0x12053013` belongs
to the experimental Router-payload wrapper, not a newer Client firmware.
Do not alter cli10's sealed version to match it.

## What the version history actually contains

| Version | Relevant change/evidence |
| --- | --- |
| cli6 | KitchenLeft's installed historical PM Client; source synchronization included the original 60-second keepalive setup. Do not count later checks/fast polling as already installed. |
| cli7 | Separate hardening branch: board-specific pin-map validation at boot/write and invalid persisted relay/button-setting handling. Not an ancestor containing all later Client fixes. |
| cli8 | Integrated 60-second keepalive re-verification with retry, fast polling during OTA and restoration afterwards; retains earlier deferred OTA-abort query recovery. |
| cli9 | Adds bounded legacy PM migration attempts and fallback/quarantine after persistent errors. This fallback can discard legacy records; verify energy/settings retention rather than assume it. |
| cli10 | Adds OTA-query startup ordering after stack initialization/join and SDK PM cluster handlers. Companion converter change provides fixed PM scales so configure does not depend on successful scale reads. |

Sources: `c8694cac`, `ee24508f`, `2ab3e635`, `e31ddada`, `ba0513d3`,
`e5d30040`, and the registry/build scripts. The claimed failed read fix was based
on reporting-configuration status 139, not a genuine attribute read; see the
[corrected investigation](bseed_pm_client_defects_20260926.md).

Important release gap: diffing the separate cli7 hardening commit `e41f499e`
against current source confirms its exact socket pin-map boot guard and some
persisted relay/button-setting sanitization are absent from cli10. Do not label
cli10 "all historical fixes consolidated". These guards are not proven fixes
for KitchenLeft's downlink issue and do not justify prolonged cli6 testing.
Before freezing the fleet release, explicitly decide whether to integrate them;
if integrated, build a new identity and validate that exact final image.

## Efficient sequence

1. **Short pre-upgrade baseline only.** Pin actual Client identity/query tuple,
   preserve settings, energy, calibration/cache and existing campaign evidence;
   establish bounded bidirectional reachability and exact-image OTA eligibility.
   Reconcile the old unverified campaign without bypassing its lock. The owner
   reports no connected appliance and can attach a test load later. Do not
   demand that old cli6 pass the new firmware's full acceptance suite first.
2. **Same-role upgrade.** Use the existing exact-target campaign runner and
   verified Client artifact. Existing cli6 executes this transfer and application;
   cli10's fixes cannot help before it boots. Preserve pacing/transaction evidence,
   then verify actual build, query version, EndDevice role and retained settings.
3. **Test firmware plus converter.** Confirm the intended converter is loaded and
   routes relay operations to EP2. The observed missing `state` converter must
   not masquerade as device downlink failure. Verify actual EP1 Read Attributes
   responses, device-side reporting/bindings, unsolicited reports and HA units.
   Stored byte-reversed binding addresses are a hypothesis to verify, not proof
   requiring pre-emptive rebinding. KitchenLeft currently has no percentage
   calibration options; preserve a snapshot and check effective scaling anyway.
4. **Use the test load when needed.** Check relay/button operation, known-load
   metering, loaded-to-zero reports and cumulative energy. Separately exercise
   same-network restart/reconnect, safe parent-loss recovery and OTA responsiveness.
5. **Fresh commissioning is a separate gate.** An upgrade preserves existing
   scale caches/bindings, so it cannot prove provisioning from an empty cache.
   Plan a controlled fresh-join test with preserved settings/history, or use a
   spare. Do not delete/reset KitchenLeft merely to start the upgrade campaign.
6. **Promote the exact tested combination gradually.** Proposed first soak is
   48–72 hours, followed by 2–3 matching PM sockets across different parents.
   This is an engineering acceptance proposal, not a guarantee from elapsed time.
   Non-PM variants require separate validation. Any later firmware/converter
   change must be assessed and tested before promotion.

The sustained acceptance work belongs after the upgrade. This plan performs no
live flash, relay operation, configuration write or calibration change.

## Separate proof for attributes, reports and downlink

- Capture transaction-matched EP1 Read Attributes replies for voltage, current,
  power, cumulative energy and all eight multiplier/divisor attributes. Expected
  ratios are voltage 1/100, current 1/1000, power 1/1 and energy 1/1000.
  Converter caches and MQTT messages do not substitute for wire replies.
  Record command IDs: status 139 on Read Reporting Configuration is NOT_FOUND;
  do not report it as an unsupported Read Attributes response.
- Read back all four reporting rules and bindings after ordinary configure,
  then observe unsolicited periodic and load-change reports without issuing
  GETs during that observation window. Allow the complete maximum interval
  (energy 600 s) plus a documented margin. Correct scaling before configure
  and from empty/stale caches must be exercised separately.
- Verify a fresh EndDevice descriptor with Rx-on-when-idle set. Do sparse,
  timestamped EP2 relay reads and EP1 meter reads throughout the 48–72 hour
  soak, including first reads after periods without diagnostic traffic. Avoid
  a tight probe loop that could hide idle-triggered failures. Telemetry or
  `online` alone cannot pass this gate. Record every timeout, recovery and
  parent change; unexplained sustained downlink loss fails acceptance even
  while uplink reports continue.
- Exercise safe parent-loss/rejoin and reboot recovery separately with an
  approved fixture; repeat reads and unsolicited reports afterwards. Check
  OTA query responsiveness before and after idle/recovery. A further test OTA
  requires its own exact artifact/device authorization; new source alone
  does not prove the SDK actually polls at the configured rate.
