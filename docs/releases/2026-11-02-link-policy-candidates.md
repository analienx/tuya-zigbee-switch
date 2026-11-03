# Candidate release notes · 2026-11-02

**Shared parent and route link policy for BSEED TS011F sockets**

> [!CAUTION]
> **Sealed firmware candidates, not a public OTA release.** These four images are not in `zigbee2mqtt/ota/index_bseed.json`. Hardware acceptance, per-device functional tests and fleet authorization remain separate. Do not present this page as permission to flash or to upgrade a running coordinator.

[Current firmware status](../bseed_socket_release_status.md) · [Policy design](../bseed_link_policy.md) · [Historical firmware changes](../changelog_fw.md) · [Release procedure](../contribute/release.md)

## At a glance

| Board and role | Sealed candidate | Native FILEVER | Custom OTA image type |
| --- | --- | --- | ---: |
| PM TS011F-BS-PM / Router | `1.2.5-bseedr19` | `0x12053021` | 43556 |
| PM TS011F-BS-PM / mains Client | `1.2.5-bseedcli22` | `0x12053021` | 65024 |
| Non-PM TS011F-BS / Router | `1.1.3-bseedr18` | `0x1102301D` | 43555 |
| Non-PM TS011F-BS / mains Client | `1.1.3-bseedc16` | `0x1102301D` | 65026 |

**Exact identity:** PM `b28wrpvx / TS011F-BS-PM`; non-PM `o1jzcxou / TS011F-BS`. Both socket families use Telink firmware but have **different power-monitoring hardware, OTA types and board-specific configuration**. Router and Client share a version counter *within the same board family* but never share the same OTA image type.

## What changed

### More deliberate parent choice for mains Clients

- Remain on the current parent during ordinary operation to avoid unnecessary mesh churn.
- After **three parent-loss signals within 60 minutes**, switch the next SDK-owned rejoin into parent-rescan mode rather than repeatedly preferring the old weak parent.
- Return to the sticky-parent policy after **six hours without another loss**. Repeated signals belonging to the same SDK backoff episode do not count as separate failures.

### Router route quality and recovery

- Observe the active next hop toward the coordinator and alternate-neighbor costs.
- Request a **targeted** route refresh when measured conditions justify it; bound repeated refreshes with cooldowns.
- On the existing Router rejoin path, rescan rather than locking back onto a demonstrably poor route. Existing rescue protections remain bounded; no global mesh reset or factory reset is introduced.

### Diagnostics and exclusions

- Add the read-only Basic-cluster `0xFF13` link snapshot, including policy flags, parent/next hop, link cost and recovery counters. Read it separately from `0xFF10` and `0xFF12` on Clients.
- Never start a new link-policy action while OTA or an SDK-owned rejoin sequence owns the radio.
- Keep board-specific relay and PM behavior separate from shared route/parent decisions. Older sealed images remain immutable.

See the [design contract](../bseed_link_policy.md) for thresholds, timers and byte layout rather than relying on this short summary.

## Provenance and sealed artifacts

- **Candidate source:** [`1cc08a92d4dd68ff3607dc224f707c3e757d4ec8`](https://github.com/analienx/tuya-zigbee-switch/commit/1cc08a92d4dd68ff3607dc224f707c3e757d4ec8)
- **CI:** [candidate finalization](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449369099), [host tests](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368782), [PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368902), [non-PM matrix](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368974), [paired Router TC32](https://github.com/analienx/tuya-zigbee-switch/actions/runs/41449368806)
- **Identity registry proposal:** SHA-256 `258c8171f4c0357da44dddbf3717624191256df91bc703e8a49b41531b384edc`, committed without modifying previous sealed identities

| Native forward OTA | SHA-256 |
| --- | --- |
| PM Router r19 | `d17571a1d996ff798f1b6334cac1d799fbc1c6125a0212741aca89446053fc95` |
| PM Client cli22 | `a8d2e38f53c63a4f0561076c1c561a1917f25ece602b16d256ff1b1c36a7bbf8` |
| Non-PM Router r18 | `4fcc69b1113cbe405ea0aef9ed9ae81b8f13d61147b227671f00b8764cf063e9` |
| Non-PM Client c16 | `d4057825661eb4a6c943ca4f8a5b02d7c709acdb960e4102fd5a185e4f87e321` |

These hashes identify **native OTA forward images**, not stock-conversion wrappers or a generic flash package. CI and sealed bytes establish integrity/reproducibility, **not live hardware acceptance**.

## Deployment and acceptance

| Gate | Required evidence |
| --- | --- |
| Exact board and role | Fresh model, manufacturer, IEEE, installed build, expected image type and sealed hash |
| Safe network window | No competing OTA, closed permit-join and no coordinator/Zigbee2MQTT maintenance in progress |
| Single-device OTA | Transaction-matched response, active lock, integrity, no unchecked automatic retry |
| Postflash | Reinterview and new Basic build, preserved Zigbee role, relay policy and settings |
| PM boards | Fresh raw metering/reporting, power/load test and nondecreasing cumulative energy |
| Router | Real child routing, recovery/route quality and soak |
| Mains Client | Parent reassignment after loss, healthy stable connection and soak |

**Open:** independent physical-load acceptance and sustained mesh/parent recovery across each of the four variants. A successful individual OTA or improving network metrics must not silently promote the whole set. If coordinator firmware is changing, suspend canary operations until the coordinator and Zigbee network have recovered, then begin with a fresh preflight.

## Publication status

The [public BSEED index](../../zigbee2mqtt/ota/index_bseed.json) remains unchanged. It advertises the older accepted Router builds and exact stock-to-custom wrappers, **not** these candidates. Promotion requires explicit acceptance evidence and a separately reviewed index update. Earlier candidates remain part of the historical record; do not re-seal different bytes with their old identities.
