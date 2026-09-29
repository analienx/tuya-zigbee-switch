# Non-PM `cli5-rc1` preflash code review — 2026-09-21

**Disposition: source/image checks pass; physical-flash recovery is NOT proven. No live flash was performed.**

Target is `BedroomSocketCabinetRight`, BSEED TS011F-BS / `o1jzcxou`, Telink TLSR8258 **non-PM** mains Client. The distinct, reportedly dead PM socket is NOT an OTA recovery fixture, equivalent board, or evidence that this non-PM candidate fails the same way. Do not confuse the variants.

## Reproduced build and delta

- Rebuilt `cli4` from `ecd40fbc` in an independent, isolated Linux worktree; baseline OTA package 158,338 bytes, native Client image type `65026`, version `285356047`, NVM schema `1`.
- Staged `cli5-rc1` is from clean `0fc6a7ed`, package 158,578 bytes, image type `65026`, version `285356048`, same board config and NVM schema `1`. Candidate package SHA-256: `92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd`. No role or NVM migration is intended.
- Shared source changes since baseline: `zigbee_network.c` configures a 60-second EndDevice MAC poll on joined/init and commissioning success; `zigbee_ota.c` expands deferred abort requery to PM Routers (non-PM Client already had it); `zigbee_zcl.c` adds PM-only cluster attribute registration; `relay_cluster.c/.h` add a read-only logical relay level attribute. `make_ota.py` adds a separate reseal command, not a change to the normal `create-ota` path.
- An OTA package is not a byte-for-byte patch of the installed binary. The reported installed `cli4` build ID cannot independently establish its full source hash; the isolated `ecd40fbc` build is the repository's release-baseline reference, not a readback of the live chip.

## Binary and tooling gates

- Verified staged and private-server copies agree; outer manufacturer `4417`, Client image type `65026`, version `285356048`; single type-0 sub-element size, embedded Telink startup flag/version/length and payload CRC agree.
- `bseed_targeted_z2m_ota.py` now checks the inner TLNK marker, version, sub-element length, embedded size and CRC **for explicitly opted-in non-PM Client images**, including `prepare`, `check` and `flash` paths. This does not alter PM OTA framing or the candidate binary.
- Package size is below the conservative 208-KiB limit used for the 512-KiB Telink OTA layout; this is a size check, not independent confirmation of the exact physical flash part, NVM preservation or successful boot.
- The short live link-gate → targeted read-only OTA check → link-gate sequence passed once after earlier failures. It proves only contemporaneous communication; it neither proves long-term parent stability nor establishes a reliable firmware-transfer or reboot path.

## Specific remaining risks / recovery gate

1. The new 60-second poll is requested only after a successful joined callback, consistent with the Telink API contract. Its return code is only printed over UART; neither successful scheduling, actual parent MAC-poll traffic nor rejoin to an alternative Router has been observed on this non-PM hardware. Do not promise that this fixes the flashing LED or restores parent reliability.
2. The golden **non-PM Router** rollback uses image type `43555` (or a stock-facing alternative), while this Client accepts type `65026`; a saved Router image alone is NOT a verified Client→Router OTA rollback. OTA also requires a responsive device; an unbootable or unjoined Client cannot be recovered by MQTT. Do not describe an unrelated PM socket's binary or clip as this model's recovery plan.
3. The Telink TLSR8258 has on-chip serial flash and uses its specific SWire/SWS programming path. Possessing a generic SOIC clip does not demonstrate access to this socket's in-circuit debug pads or a correct 3.3-V/logic-level, mains-isolated programming arrangement. No whole-flash readback from **this exact non-PM socket** or dry-run restored image has been verified.
4. Current software reports relay `OFF` and physical-relay policy `follow_state`, but the attached physical appliance, acceptable restart behavior, and means to recover a stuck-ON/OFF contact are not established. Neither fresh MQTT nor an OTA query proves the mains outlet can be safely interrupted.
5. The current flash runner's private per-campaign lock does not establish an exclusive fleet-wide Zigbee OTA transaction or a physical programmer fallback. Confirm live network-wide OTA inactivity immediately before any authorized attempt; a new workdir must never bypass a previous incomplete campaign.

**Decision: NO FLASH under the user's non-bricking objective until** the exact non-PM board's safe physical-load/isolation plan is established and recovery hardware is validated against its real programming interface. Preserve the untouched `cli5-rc1` candidate and prior failed PM evidence independently. A later separately authorized single-target update would require fresh no-flash qualification, expected version/role and 32-byte blocks, then one targeted re-interview and independently verified physical relay/network behavior. A completed transfer is not acceptance.

This is a firmware/host-code review and documented risk gate, not a guarantee against device failure or a certification of electrical safety. Do not power a programmer or attach a clip to a mains-energized outlet PCB.

### Enforced follow-up hardening

The `NO FLASH` disposition is now enforced in BOTH OTA entry points, not merely documented. The separately versioned firmware bytes are unchanged. See `docs/bseed_nonpm_cli5_hardening_20260921.md` for exact private evidence schema, the immediate physical-load confirmation requirement, fail-closed file/board checks and the remaining unproven hardware recovery limitations. Existing PM firmware/campaigns are outside this non-PM gate.
