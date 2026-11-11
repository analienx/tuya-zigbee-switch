# SWire recovery logic and agent-interface review

This review covers the recovery protocol as well as code paths. All execution
evidence comes from GitHub-hosted runners at the candidate SHA. No physical device
read, write, boot or electrical qualification is claimed by this record.

## Findings and repairs

| Finding | Repair | Regression coverage |
| --- | --- | --- |
| A CP2102 junction echo could be mistaken for a target reply | Separate adapter/target diagnostics; exact echoes, repeated register responses, halted-only small flash reads; no reset or flash-write commands | Echo-only, missing/partial replies, bad echo, changed CPU state and unstable flash responses |
| B/full-slot JSON plan returned default-A addresses | One selected runtime plan, numeric ranges, preserved ranges and confirmation tokens | JSON plan matches the actual B write bounds |
| Separate activation reset the CPU before HALT | No-reset attach reasserts HALT and configures SWire | Separate stage/activate reset count; held and unheld activation |
| Stage receipt could authorize repeated or interrupted activation | Durable session phases; activation consumes stage permission | Completed replay opens no hardware; new IDs cannot bypass uncertain phases |
| Missing JSON on parser errors; generic errors hid possible writes | Parser errors use the response envelope; uncertain writes have dedicated error codes, evidence and exit status | Missing arguments, invalid choices, interactive-terminal JSON and interrupted writes |
| Reader diagnostics were discarded | Private bounded logs, exit codes and a pre-work read receipt | Reader failure, timeout, mismatch and short reads |
| Board and role checks could disagree with native BSEED identity | Exact PM/non-PM and Router/Client tuple checks; guarded profiles cannot be bypassed via generic | All four native identities and mismatch rejection |
| In-place slot choice could contradict the backup | Baseline must establish the selected unique enabled slot; full-slot erase is required | Wrong area and partial erase rejected before hardware |
| Historical r19 fallback was blocked by the modern evidence format | Separate explicit rollback profile pinned to original container, native bytes, source and identity; current candidate gate stays strict | Exact legacy payload acceptance, wrong images rejected and optional original-hosted-artifact cross-check |
| Optional readback was not integrated into activation eligibility | A supplied baseline requires session verification; activation additionally requires hold | Activation blocked before verification and after a failed verification |
| An old successful read could undo a newer failure | Evidence must postdate the latest phase; exact completed verification can be safely replayed | Old-good/new-bad regression; verification replay |
| Separate work directories could bypass a previous operation | Shared per-port authority plus immutable session context | Different/copy session cannot take ownership or write |
| Stale-lock release could race a new owner | Short OS-owned metadata lock serializes create, remove and release | Metadata contention, dead/live owner release and preserved lock evidence |

## Recovery invariants

1. A and B are alternate application slots, not interchangeable records of what
   ran. Slot identity comes from actual matching double reads and known image
   comparisons, never cached Zigbee metadata or OTA transport success.
2. The firmware is the finalized native payload, with native tuple, length and CRC
   checks. OTA wrappers and pre-packaging binaries cannot substitute for it.
3. Every erase and program is bounded to the selected application slot. The other
   slot, NVM, IEEE and factory regions are preserved. Full-slot verification checks
   the image, erased tail and all bytes outside the selected range.
4. TX-only mode remains available without readback. Its result is transmission
   evidence only. Supplying a backup chooses a verified in-place workflow and
   cannot silently downgrade to unverified combined flashing.
5. The session binds target ID, image, port, selected slot, erase extent and original
   baseline. Intent is saved before opening hardware. Errors after intent are
   conservatively uncertain, even if no flash byte was ultimately changed.
6. A completed request is replayed only for the same session, operation ID and
   exact arguments. An uncertain request is never automatically repeated. Fresh
   readback reconciliation permits a new activation only when the complete staged
   image and required preserved regions match.
7. Failed verification blocks activation. A fresh no-reset double read is needed
   to reconcile or recheck it. Session closure records an assessed stop; it does
   not change hardware, prove recovery or retire OTA campaign locks.
8. A process lock protects hardware access. Durable ownership prevents another
   session from bypassing it. Crash locks are retained until explicit dead-owner
   release; releasing a lock does not reconcile the recovery state.
9. A held activation can be byte-verified before a controlled bench start. Actual
   boot, expected Zigbee identity/role, relay behavior and metering remain separate
   live acceptance evidence.

## Practical limits

The protocol cannot identify a chip electrically through a TX-only connection.
The operator must confirm the actual attached target and retain the same physical
setup throughout a session. Bidirectional reads require the documented compatible
circuit; a UART loopback does not establish that capability. Sending HALT or an
activation marker is not proof the target received it.

The regression suite runs on hosted Linux and Windows. The UART/SPI/NOR model
checks storage effects and fault handling; it does not qualify an adapter, supply,
pad contact, boot ROM or physical board. PM/non-PM role matrices produce separate
exact-commit wired kits. Green software evidence does not authorize a hardware
write or establish physical recovery.
