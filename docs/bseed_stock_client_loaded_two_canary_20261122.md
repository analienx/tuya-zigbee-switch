# Two exact stock PM Router → custom Client cli27 preserve-ON canaries

**Status:** experimental, requires exact-source hosted CI and separate per-device
software and hardware acceptance. Not a fleet-wide stock conversion or public
OTA index update. Operator authorization: 22 November 2026.

## Scope and immutable firmware

- `HALLSocketMain` IEEE `0xa4c138186a69b78c`
- `LivingRoomSocketWifiRight` IEEE `0xa4c138b5e090e7c0`
- Both must be interviewed stock `_TZ3000_b28wrpvx / TS011F` Routers
  with a **freshly read ON** relay. Target `b28wrpvx / TS011F-BS-PM`
  mains Client `1.2.5-bseedcli27`.
- Transport stock-facing OTA wrapper `f3faa5fdeb90dd3451108a11debd2c24c20b9cf658f7bf6fa01c438f6e60f31b`,
  stock type 54179, file version 0xffffffff, and verified native image
  `0c762af950cdd791fb561dc3d219c745f294b11e075f55b3f735ebc059308ee1`.
  Header-only wrap is still proved by the existing version policy.
- `stock_loaded_preserve_devices` must opt in the exact target names in
  **private** site settings; the code pins both names **and** IEEE identities.

## Why an exception is required

The historical stock conversion automatically switches the relay OFF, then
uses that fresh OFF read instead of a human physical-unplugged attestation.
The operator's current policy is **never to switch connected appliances OFF
for OTA**. Both nominated sockets returned ON in read-only Zigbee2MQTT GETs
on 22 November at 19:47 CET: HALLSocketMain 0 W; WifiRight about 7 W.

Stock firmware does not provide a trustworthy converter-stamped independent
ZCL PM freshness proof. The new route therefore records
`operator_waived_unknown_power`, not 0 W or `physically_unloaded`. The
operator authorized loaded flashing; this is a risky exception and a firmware
reboot **may interrupt mains output even though we do not send a relay SET**.
If uninterrupted supply is mandatory, do not run the conversion.

## Safety envelope

- Check fresh source manufacturer, model, hardware, IEEE, role, interview,
  relay ON and correct served SHA. Do not flash a different stock variant.
- Disable automatic relay-OFF completely. Pin `expect_relay=ON` on
  the **stock** `state` key; never send `state=OFF` for this procedure.
  The stock boot may nevertheless reset the output OFF. After confirmed
  custom firmware rejoin, the runner must read `state_relay`, restore
  **ON only** if necessary, and record the interruption as negative
  physical-continuity evidence. No relay command is sent before reboot.
- A direct, current OTA availability check **must** pass before flash.
  Reject mismatched image URL, version, role, shared-network lease or current
  firmware. Preserve bounded retries and immutable failure evidence.
- `relay_state_policy=preserve`, explicit waiver and `max_reported_watts=100`
  (structural cap, not proof that stock meter is fresh). Only the approved
  two identities can use the waived route.
- Following the role change, check custom `state_relay` for ON, verify
  build cli27, EndDevice role, PM provisioning, Zigbee parent/child behavior
  and 24 h stability. A failed postflash check must **not** be recorded as
  hardware acceptance.
- WifiRight is a current parent of `BedroomCabinetHumidityTempL1` and
  `HallPIRSensorMain`; they must find another parent. HallMain previously
  had no dependents in its readable neighbor table. Re-check immediately
  before launch.
- No duplicate OTA requests to a socket, no router conversion from this
  Client wrapper, and no relay-state toggles.

## Operator commands

Use `bseed_socket_migrate.py --site PRIVATE_SITE plan --device NAME --to client`,
then `publish`, then `run`. The default stock conversion is **not** safe
for an ON relay: verify the private profile has
`stock_client_preserve_loaded=true`, `auto_relay_off=false`,
`relay_state_policy=preserve`, `expect_relay=ON` and
`operator_waive_preflash_pm=true` before any `run`.

The existing migration runner serializes with held network leases. Do not
circumvent a running parent/neighbor OTA without checking current mesh and
the coordinator's shared campaign locks. Each completed transfer needs
independent postflash verification and network-lease reconciliation.
