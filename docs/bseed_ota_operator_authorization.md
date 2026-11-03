# BSEED non-PM OTA operator authorization policy

**Updated 2026-11-03.** A user's explicit acceptance of unrecoverable-device risk is an **operator risk authorization**, not hardware recovery evidence or permission to weaken firmware integrity.

## No repeated irreversible-loss prompts

When an operator has already granted risk acceptance for an identified socket, **do not keep asking them to repeat the same permanent-loss sentence**. Record the authorization once, with the device IEEE, installed version, proposed version, candidate SHA-256 and scope. A subsequent invocation of the **same** exact authorized operation should consume the recorded approval without asking again.

For future images on the same socket, check the existing operator's standing preference first. A new candidate must still undergo independent candidate/digest review and the code's explicit admission; this review must not be represented as a request to re-utter the permanent-loss wording. Do not turn the preference into a fleet-wide unsafe fallback.

**Physical load disconnected confirmation remains per campaign**, because it is an actual state that can change between OTA attempts. The 2026-11-03 user confirmation applies to the bedroom pair while they remain unchanged/unloaded. The OTA command still requires the `--confirm-load-unplugged` flag; this is a physical precondition, **not** a repeated irreversible-loss consent dialog.

## Pinned authorization: BedroomSocketCabinetRight

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

**One device at a time** is the user's current operating preference. CabinetRight is first; Balcony is next and requires its own firmware and recovery-policy review. Do not automatically infer the CabinetRight SHA-specific admission authorizes a different socket.

Hardware acceptance remains separate from a successful transfer and postflash software candidate.

Related: [issue #103](https://github.com/analienx/tuya-zigbee-switch/issues/103), [recovery gate](../helper_scripts/bseed_nonpm_recovery_gate.py).
