# BSEED FORCE hardware acceptance — 2026-09-28

This record captures the live same-device PM role-transition evidence gathered on
`KitchenSocketLeft` before continuing to the return transition. Private FORCE
wrappers, MQTT credentials and machine-local evidence stay outside the repository.

## Candidate identities

| Role | Build | Native FILEVER | Status |
| --- | --- | --- | --- |
| Client | `1.2.5-bseedcli12` | `0x12053016` | installed and exercised |
| Router | `1.2.5-bseedr9` | `0x12053016` | installed and exercised |

The Client -> Router transfer used the reviewed private FORCE wrapper around the
exact sealed native `r9` payload. The wrapper transport file version was
`0xFFFFFFFF`; this is transport metadata only and is not a published firmware
version.

## Client acceptance before FORCE

The PM Client canary had already passed the meaningful live metering checks:

- idle returned to 0 W / 0 A with plausible mains voltage;
- a ~2 kW kettle load produced plausible power/current/voltage;
- cumulative energy advanced to 0.02 kWh and did not regress;
- removing load returned power/current to zero after the normal reporting delay;
- relay control and physical-state reporting remained responsive.

The earlier long same-role CLI6 -> CLI12 OTA was also reconciled correctly:
the fixed campaign monitor expired while the OTA was still making progress;
the transfer subsequently completed and the exact CLI12 image booted.

## Client -> Router FORCE transition

The first FORCE attempt was interrupted before installation. It was reconciled
without a blind retry by proving that the exact CLI12 source remained installed,
observing a quiet no-OTA window, and confirming that the exact FORCE candidate
was still offered.

A new canonical campaign then resumed the previously transferred image and
reported progress to 100%. Zigbee2MQTT returned terminal OTA success and the
device rebooted/rejoined with the same IEEE identity.

Fresh post-rejoin evidence confirmed:

- exact build `1.2.5-bseedr9`;
- Zigbee2MQTT role `Router`;
- interview completed successfully;
- fresh ZDO node descriptor `logicalType = 1` (Router);
- `rxOnWhenIdle = 1`;
- a new network address was allocated after rejoin;
- metadata refresh reported `metadata_already_correct`.

## Router functional acceptance

Live Router reads confirmed relay and PM behavior remained operational after the
role change. A kettle load and then a lower-power power-bank load both produced
updated PM values after the normal short reporting delay. The brief initial 0 W
observation was reporting latency, not a stuck metering path.

The strict settings audit found one byte-level drift:

- endpoint 2, `genOnOff`, attribute `0xFF02` changed `0 -> 1`.

That attribute is the manual Indicator LED state. Indicator mode itself remained
`0` (`same`, LED follows relay state), so the changed manual LED value is
currently inert and does not alter relay or mains behavior. All other sampled
settings persisted.

## Router child-join acceptance

A fresh IKEA RODRET E2201 was paired while permit-join was scoped only through
`KitchenSocketLeft`. The device joined and completed interview successfully as
an EndDevice and was identified by Zigbee2MQTT as a supported IKEA RODRET.

This is direct hardware evidence that the `r9` image is operating as a real
Router capable of accepting a fresh sleepy child, not merely advertising Router
metadata.

## Tooling follow-up

The canonical OTA command correctly owned the transfer and wrote terminal OTA
success, but the outer `transition` orchestration exited after
`ota_transfer_ok_postflash_unverified` before recording its intended rejoin /
metadata / Router-audit evidence. The device itself had already rejoined cleanly.

For this run, metadata, live ZDO role and PM/relay acceptance were completed
separately without reflashing or reopening permit-join. The orchestration gap
should be fixed before relying on `--mode transition` as a fully self-contained
role-transition acceptance record.

## Current gate

PM Client -> Router hardware acceptance is **passed for the tested canary**,
subject to the noted inert LED-state persistence drift and orchestration follow-up.

Next planned hardware step: preserve this evidence, then FORCE the same socket
from exact `r9 / Router` back to exact `cli12 / EndDevice` and repeat the
Client-specific acceptance gates.
