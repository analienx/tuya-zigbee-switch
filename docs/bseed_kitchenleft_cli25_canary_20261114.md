# KitchenSocketLeft PM Client cli25 — operational canary (2026-11-14)

**Outcome: Working as expected on the operator's KitchenSocketLeft.** The operator confirmed the installed canary's present behavior is satisfactory on 2026-11-14. The native OTA transfer completed, the intended firmware is running, a targeted Zigbee interview succeeded, and postflash role and power-monitoring probes passed. This is **single-device operational confirmation**, not publication or general fleet acceptance.

## Exact identity and image

| Field | Verified value |
| --- | --- |
| Device | `KitchenSocketLeft` |
| Zigbee IEEE | `0xa4c138241e3de538` |
| Hardware | PM `b28wrpvx / TS011F-BS-PM` |
| Zigbee role | Mains-powered `EndDevice` (receiver on when idle) |
| Before | `1.2.5-bseedcli22`, native FILEVER `302329889` |
| Installed | **`1.2.5-bseedcli25`**, native FILEVER `302329892` (`0x12053024`), OTA image type `65024` |
| Pinned OTA SHA-256 | `4246f097a8927fcfa9a02d16d7ee966cc96ea14487681b2cd9f34b50b41dc914` |
| Candidate source seal | `720aa6b8` (2026-11-13); not in the public BSEED index |

## On-device observations

- **Transfer completed:** Zigbee2MQTT returned an exact-device OTA `status: ok` at **14:05:54 Europe/Prague**, upgrading `302329889` to `302329892`. Total progress reached **100%**. No second firmware flash is needed.
- **Retained-image recovery:** after partial-transfer stalls, the same image was resumed with peak progress **10.70% → 13.06% → 58.70% → 100%**, using the progress-gated supervised recovery procedure. An earlier source-link failure required operator power cycling; successful later resumption was validated on the same IEEE.
- **Postflash operation:** Zigbee2MQTT logged successful configuration at **14:06:13** and a successful targeted interview at **14:06:28**. The independent read-only check at **14:33:35** saw the exact `cli25` build, an interviewed EndDevice, fresh live ZDO descriptor (`logicalType=2`, `rxOnWhenIdle=1`), and plausible PM readings. A fresh power read at **14:30:56** returned **0 W**.
- **Operator assessment:** confirmed that the socket is **working as expected for its current intended use**. This closes the practical *is this firmware operational on this socket?* question, not the full BSEED hardware promotion checklist.
- **Protocol informational message:** Zigbee2MQTT reports this Telink device as compliant with Zigbee specification revision **21** while the current controller recognizes **23**. This is a stack-revision compatibility notice, **not** the `cli25` firmware release version and not an observed blocker for interview or operation.

## Remaining evidence and scope

The independent postflash verifier returned **`unconfirmed`**, solely because the reported `state_relay` changed **OFF before OTA → ON after OTA**. The operator subsequently confirmed current behavior is expected; that statement is not proof that physical contacts were tested or that automatic relay-state retention passed. The sampled postflash LQI was low (**7**) and does not establish reliable sustained mesh behavior. Long-term parent/rejoin soak, physical relay retention and calibrated loaded metrology were not independently accepted in this record.

**Disposition:** `cli25` is **installed and operational on KitchenSocketLeft, operator-confirmed**; broader PM Client hardware release acceptance and public OTA publication **remain pending**. Do not alter sealed image identities or publish fleet updates based on this single canary. Earlier partial-OTA failure evidence remains privately preserved for engineering diagnostics.

[Current candidate matrix](bseed_socket_release_status.md) · [OTA recovery procedure](bseed_ota_resume_supervisor.md) · [PM acceptance requirements](bseed_pm_e2e_acceptance.md)
