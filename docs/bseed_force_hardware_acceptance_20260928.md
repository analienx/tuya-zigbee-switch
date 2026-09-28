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

## Router -> Client FORCE return

The same socket was then returned from exact `1.2.5-bseedr9 / Router` to the
same sealed `1.2.5-bseedcli12 / EndDevice` payload using the private FORCE
transport. Zigbee2MQTT reported terminal OTA success and the same IEEE remained
present.

Fresh return evidence confirmed:

- exact `1.2.5-bseedcli12` build and `EndDevice` inventory role;
- fresh live ZDO `logicalType = 2` with `rxOnWhenIdle = 1`;
- interview complete and metadata already correct;
- fresh non-retained PM state within the conservative 75-second verifier window;
- 0 W / 0 A idle with plausible mains voltage;
- cumulative energy retained and advanced to about 0.033 kWh;
- current PM bindings/reporting point only at the current coordinator and retain
  the 60 s active-power fallback plus current/voltage/energy reporting;
- no target-related Zigbee2MQTT errors during the postflash verifier.

The strict settings comparison again found only the manual Indicator LED state
attribute changing, this time `0xFF02: 1 -> 0`. Indicator mode remains `same`,
so this byte is inert in the active configuration. All other sampled settings
persisted.

No second kettle/load cycle was required for the return because the destination
is the exact same sealed CLI12 payload already exercised on this same socket.
The return verifier instead required fresh PM telemetry and role/build evidence.

## Current gate

The PM same-device A/B/A role matrix is **hardware-verified for this canary**:
exact `cli12 / EndDevice` -> exact `r9 / Router` -> exact `cli12 / EndDevice`.
Both PM native role candidates may therefore be treated as the current golden
hardware canaries for further PM rollout evaluation.

This does not erase the separate fleet gates: longer soak, broader mesh behavior,
explicit parent-loss/rejoin stress if required by the rollout policy, and the
separate unattended fixture-driven PM release test remain distinct from this
role-matrix acceptance.
