# BSEED PM socket: automated release acceptance

## Current blocker (2026-09-20)

`LivingRoomSocketHifiLeft` running `1.2.5-bseedcli6` measured 0 W and 0 A on
an explicit ZCL read after the charger was unplugged, but sent no fresh
standard-property MQTT state during the preceding 38-second passive window.
The earlier 25 W value stayed visible until the explicit read. The converter
fix in PR #47 maps metering onto `power`, `current`, `voltage`, `energy`; it
**does not** repair absent spontaneous reporting. This Client remains a
**NO-GO for mass firmware rollout**, regardless of successful OTA or reads.

Originally, `activePower` had minimum 10 s, change threshold 5 W and maximum
65000 s (about 18 hours). This was an inadequate freshness fallback. An
instrumented 30-second canary delivered a raw Zigbee
`attributeReport(activePower=0)` after 30 s. On 2026-09-20 HifiLeft was
reconfigured to minimum 10 s, maximum 60 s and threshold 5 W; a second raw
`attributeReport(activePower=0)` after 60 s confirmed periodic reporting.
The bounded converter is also live. These periodic reports **do not prove**
that a 25 -> 0 W load change is reported promptly. Change-triggered reporting
requires a repeatable physically controlled load cycle that first establishes
the loaded state through an unsolicited device report.

## Three independently evaluated test layers

1. Offline CI: `python -m pytest -q tests/test_live_bseed_pm_metering.py`
   verifies that cached, retained, stale, malformed and wrong-endpoint values
   cannot satisfy the gate. Existing client role, Telink cluster registration,
   relay and firmware-build tests must also pass for the affected target.
2. Hardware canary: `tests/live_bseed_pm_metering.py` exercises one identified
   BSEED socket and a **separate, independently controllable, safe test load**.
   It must keep the DUT energized, connected and relay ON. It never resets,
   flashes or toggles the DUT relay, and only does a baseline ZCL read BEFORE
   the on/off test. The loaded and unloaded phases accept only newer,
   non-retained MQTT readings, with no intervening DUT reads by the runner.
3. Home Assistant: run the same cycle with `--require-ha` and a read-only HA
   API token. Both loaded and unloaded values must appear on the existing
   standard power sensor, not newly named `_switch` entities.

## Live gate contract

The fixture must use an independent MQTT `/set` topic and switch only a
known low-risk test load downstream of the DUT. A charger plugged into the
socket without a separately controllable safe test fixture is **not** an
unattended automation. Never use the DUT relay, its mains feed, an upstream
breaker, or an unrelated household appliance as the test-load controller.
The fixture command payload and HA token are not stored in the repository.

Example command structure; replace the fixture and secrets with the actual
authorized test setup. Do not run the placeholders verbatim:

```bash
BSEED_MQTT_USER=... BSEED_MQTT_PASSWORD=... BSEED_HA_TOKEN=... \
python tests/live_bseed_pm_metering.py \
  --device LivingRoomSocketHifiLeft --expected-role EndDevice \
  --broker YOUR_Z2M_HOST --fixture-topic 'zigbee2mqtt/DEDICATED_TEST_FIXTURE/set' \
  --fixture-on-json '{"state":"ON"}' --fixture-off-json '{"state":"OFF"}' \
  --allow-fixture --ha-url 'http://YOUR_HA_HOST:8123' \
  --ha-power-entity sensor.livingroomsockethifileft_power --require-ha
```

Pass conditions: installed `-bseed` firmware, expected Zigbee Router/EndDevice
role, standard PM keys, relay ON throughout, idle initial baseline, fresh
power >= 5 W and current > 0 A under fixture load, subsequent fresh power
<= 1 W and current <= 0.02 A after fixture OFF within 35 s, plausible AC
voltage, no energy decrease, and an unsolicited energy increase >= 0.001 kWh
within a bounded loaded window. Check the real HA sensor independently.
The firmware's configured `activePower` maximum must be <= 60 s as a
bounded-loss fallback; this is a policy gate, not evidence that reports fire.
The supported PM outlet converter scopes this to one model only, with 300 s
fallbacks for current/voltage and 600 s for energy, while preserving the
10 s minimum and the change threshold. Avoid globally applying these
intervals across a large Zigbee mesh.

The baseline ZCL read is intentionally **not** counted as an automatic-report
pass. Neither are a retained MQTT payload, a forced device `get`, successful
OTA, a `device/configure` ACK, or an endpoint merely exposing a sensor.

## Broader firmware acceptance matrix (separate from metering)

Before any multi-device Client rollout also gate: safe relay ON/OFF and
physical-button events; persistent device configuration, bindings and relay
state after reconnect; stable parent/rejoin and radio traffic under ordinary
mesh load; OTA progress, verified image identity and recovery after an aborted
transfer; and Router-versus-Client separation without destroying the retained
Zigbee network. Keep firmware artifacts and converter revisions versioned
and independently reproducible. None of these can be inferred from the
offline MQTT-gate tests or a successful read of `activePower`.

Record the installed image version, device IEEE, Zigbee role, endpoint/binding
and configure-reporting snapshot, test-fixture identity, event timestamps,
raw ZCL report trace if available, MQTT standard properties, and HA entity
states for each canary. Never publish MQTT credentials or HA tokens in logs.
If the Zigbee application does not spontaneously report the unload, capture
raw `attributeReport` traffic before deciding between a Telink timer/binding
firmware fix and coordinator-side message-handling fault. A `readResponse`
from a diagnostic get is NOT an `attributeReport`.

**Current acceptance outcome:** converter mapping, direct loaded/unloaded ZCL reads, and unsolicited PERIODIC 30 s/60 s reports passed. Prompt, change-triggered reporting remains unverified; the first unplug test had no independently confirmed loaded attributeReport baseline and the live fixture-driven cycle has not run. Do not mark CLI6 fully repaired
or flash additional Clients based on the converter-only success.
