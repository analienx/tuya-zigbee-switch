# BSEED paired PM / non-PM Router contract — 2026-10-11

The BSEED PM and non-PM TS011F sockets are different boards and different OTA
release lines, but their Telink Router lifecycle is shared source. Router
reliability work must therefore be validated as a pair rather than allowing a
PM-only or non-PM-only source change to appear complete.

## Current Router lines

| Family | Board | Current candidate | OTA image type | Board-specific surface |
| --- | --- | --- | ---: | --- |
| PM | `b28wrpvx / TS011F-BS-PM` | `1.2.5-bseedr13` | 43556 | PM GPIO, BL0937 metering/protection, PM config and release identity |
| non-PM | `o1jzcxou / TS011F-BS` | `1.1.3-bseedr12` | 43555 | non-PM GPIO/config and release identity; no metering contract |

These candidate names are release metadata, not a statement of equal release
age or hardware acceptance. PM r13 and non-PM r12 remain independent OTA
identities and must never be relabelled or substituted for one another.

## Shared Router lifecycle

Both Router binaries compile the common Telink lifecycle in
`src/telink/hal/zigbee_network.c`. The following behavior must remain
board-neutral:

- factory-new devices use BDB network steering;
- previously joined devices use the SDK rejoin-with-backoff path rather than
  starting fresh steering over an existing recovery;
- steering and rejoin ownership are mutually exclusive;
- parent-loss/rejoin-failure callbacks preserve the active SDK backoff sequence
  instead of starting overlapping recovery instances;
- successful stack join clears application recovery ownership;
- Router receiver/power descriptor invariants remain role-gated, not
  board-gated;
- Router `parentInfo` continues to advertise the proven MAC Data Poll
  keepalive capability used by sleepy children.

PM-only OTA-query scheduling and metering behavior are intentionally outside
that shared recovery contract. Likewise, non-PM must not inherit PM metering or
protection assumptions.

## Mandatory paired CI

`.github/workflows/telink-router-ci.yml` is the authoritative native build gate.
Its matrix must compile both exact Router variants from the same candidate SHA:

1. PM Router — `make_scripts/build_bseed_ts011f_pm_v8.sh`, type 43556.
2. non-PM Router — `make_scripts/build_bseed_ts011f_nonpm_router.sh`, type
   43555.

The workflow's `paired-router-contract` aggregation job runs only after the
matrix and fails unless the whole matrix result is successful. It then checks
the source/release contract and the shared recovery regression tests. The
source-only entry point is `make bseed/router-pair`; under repository policy it
is validation evidence only when run on GitHub-hosted Actions against the exact
candidate SHA.

`helper_scripts/bseed_router_pair_contract.py` additionally fails if a PM or
non-PM board macro is introduced around the common rejoin, steering,
parent-loss or rejoin-failure paths. This prevents a future fix from silently
landing in only one Router family while both binaries still happen to compile.

## Identity rule for future Router fixes

A documentation/test/CI-only change does not allocate new firmware identities.
If a later change alters bytes in the shared Router firmware surface, both
affected Router release lines require fresh monotonic, non-reused identities
before release evidence may be sealed. A previously sealed PM or non-PM Router
image remains immutable.

This rule does **not** require PM and non-PM to share the same numeric
`fileVersion`; they are distinct board-wide OTA namespaces. It requires each
affected line to advance according to its own version policy.

## Hardware acceptance remains separate

Paired CI is necessary but cannot establish live Router acceptance. Each board
still needs its own hardware evidence. At minimum a Router candidate requires:

- exact board / IEEE / Router role and Basic build after boot;
- relay and settings retention;
- PM Router: real voltage/current/power/energy behavior and protections;
- non-PM Router: explicit absence of PM reporting and correct board GPIO;
- normal routing evidence, `Mgmt_Lqi` / `Mgmt_Rtg` behavior and no
  unexplained route-management regressions;
- power-cycle / retained-network recovery without destructive re-pairing;
- real child-parenting evidence when parenting/recovery code changes,
  including a sleepy child wake/action test and topology confirmation.

Do not infer PM hardware acceptance from a non-PM canary or vice versa. Do not
infer either from OTA transfer completion or a green paired CI job.
