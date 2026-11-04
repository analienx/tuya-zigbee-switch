# BSEED non-PM OTA operator authorization policy

**Updated 2026-11-03.** A user's explicit acceptance of unrecoverable-device risk is an **operator risk authorization**, not hardware recovery evidence or permission to weaken firmware integrity.

## Repository-wide, device-wide no-repeat risk policy

This policy applies to **all BSEED devices managed by this operator**, not just CabinetRight, and to **all subsequent BSEED firmware updates**. The operator explicitly acknowledged that experimental OTA can permanently render a socket unusable. Treat that acknowledgement as a **standing risk preference** and **do not ask the operator to repeat any permanent-loss consent sentence again for each socket, version or retry**.

Keep the repository warning plainly visible so other maintainers and operators understand the risk. **Do not assume that a different person installing this repository has consented**; this is the current owner's authorization, not blanket consent on behalf of third parties.

The standing acknowledgement eliminates repeated *dialog prompts*, **not** technical safety checks. Each specific OTA still requires exact verified device and role identity, cryptographically verified sealed firmware, an available Zigbee route, bounded coordinator health, exclusive network lock, and fail-closed update reconciliation. Never disable those checks, and do not imply the device can be recovered without a physical programming backup.

**Electrical load state is a separate physical check**, not a repeat of risk consent. The operator confirmed both bedroom sockets unloaded on 2026-11-03. Preserve a physical load check when circumstances have changed or before a later campaign.

## Current exact-image admission: BedroomSocketCabinetRight

| Field | Recorded scope |
| --- | --- |
| User authorization | Explicitly accepted potential permanent device loss and directed one-time OTA on 2026-11-03 |
| IEEE | `0xa4c13824a7005afb` |
| Device / board | `BedroomSocketCabinetRight` / `o1jzcxou`, `TS011F-BS`, non-PM |
| Role | EndDevice → EndDevice |
| Version | `1.1.3-bseedc9` → `1.1.3-bseedc16` |
| OTA SHA-256 | `d4057825661eb4a6c943ca4f8a5b02d7c709acdb960e4102fd5a185e4f87e321` |
| OTA image type / version | 65026 / `0x1102301d` |
| Packet size | 32 bytes |
| Recovery | No proven complete-device SWire backup; recovery is **not guaranteed** |
| Authorization consequence | Permit the exact no-backup candidate only with all canonical preflight and network gates passing |

The admission is implemented in `helper_scripts/bseed_nonpm_recovery_gate.py` using the fully pinned source/target/hash tuple. A different IEEE, firmware hash, board, role, relay policy or missing load confirmation **must still refuse**.

## Non-negotiable gates

The authorization is **not** a waiver for corrupt or unsealed images, cross-role conversion, missing IEEE/board confirmation, shared OTA lock conflicts, unreachable devices, permit-join problems, stale preflight or uncontrolled retries. Do not skip the two-stage image/hash verification, exact-size 32-byte non-PM packet constraints, bounded coordinator checks, postflash Basic/ZDO validation, or device-specific telemetry and soak.

**One device at a time** is the user's current operating preference. CabinetRight is first; Balcony is next. The standing risk preference covers the operator's entire BSEED fleet; the *technical image admission* remains exact and candidate-specific, without new risk-wording prompts.

Hardware acceptance remains separate from a successful transfer and postflash software candidate.

Related: [issue #103](https://github.com/analienx/tuya-zigbee-switch/issues/103), [recovery gate](../helper_scripts/bseed_nonpm_recovery_gate.py).
