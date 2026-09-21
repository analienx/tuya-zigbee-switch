# BedroomSocketCabinetRight: non-invasive `cli5-rc1` OTA canary

This document supersedes the **mandatory disassembly/readback** disposition in `bseed_nonpm_cli5_preflash_review_20260921.md` and `bseed_nonpm_cli5_hardening_20260921.md` for this exact canary only. Hardware recovery evidence remains an optional stronger route; no actual firmware recovery is proven.

## Distinguish failures and built-in boot behavior

- The separate PM socket reportedly ran after its last update and failed **later**. This is not evidence of a first-boot brick, a flash write fault, or a firmware root cause. Do not transfer its hardware diagnosis to this non-PM unit.
- Telink TLSR8258 multi-address Zigbee OTA alternates 0x00000 and 0x40000 slots; it validates the complete new image before selecting it. A successful transfer does not establish automatic rollback after an app crash, watchdog reset, lost parent or a physical power fault. Do not implement untested flash boot-flag changes as part of this `cli5` canary.
- `BOOT_LOADER_MODE=0` and `MODULE_WATCHDOG_ENABLE=1` are already present in source; the latter flag alone does not prove coverage of every hang or a working fallback to the previous firmware.

## Exact opt-in, no disassembly required

Both `bseed_ota_campaign.py` and direct `bseed_targeted_z2m_ota.py` enforce the same gate. The non-invasive option is **only** `BedroomSocketCabinetRight`, IEEE `0xa4c13824a7005afb`, non-PM `o1jzcxou`/`TS011F-BS`, EndDevice→EndDevice, `cli4`→`cli5-rc1` SHA-256 `92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd`; relay `OFF`/`follow_state`, `state_relay` endpoint, 32-byte blocks. No PM or different build may use this risk waiver.

The operator must **physically check that the outlet's appliance is unplugged just before flashing**, then use `--confirm-load-unplugged` plus `--accept-nonrecoverable-ota-risk` on the separately authorized exact-IEEE flash command. Risk acceptance means accepting possible permanent loss of this socket if boot or hardware fails; it is never inferred from passing tests or a saved profile. Never fabricate either confirmation or send these flags in read-only `qualify`.

Before sending any image: inspect current fleet OTA activity and previous campaign locks, preserve private Z2M/coordinator settings, verify the unchanged signed-off image/header/CRC and one-device private index, then run a **fresh** read-only `qualify` (three separated replies → OTA eligibility → three further replies). A retained `online` flag is insufficient. The second gate and successful check must remain unexpired when the one-target `flash` command executes.

One attempt only; no unattended retry, factory reset, power cycle, relay toggling or role change. Leave the old firmware, Zigbee identity and binding/settings evidence intact. The accepted post-update workflow requires a targeted re-interview, exact installed `cli5-rc1` build/role, freshly read relay and network-LED settings and sustained availability/parent recovery observation; an `OTA OK` message is not hardware acceptance.

If the image is not offered or a live GET fails: STOP, preserve evidence; do not force OTA while Client radio communication is intermittent. If an OTA aborts: preserve the transaction and block offsets; no automatic repeat. If the new build fails to rejoin or the physical button/relay stops working, stop remote modifications, distinguish radio loss from frozen device and power/hardware failure; physical recovery may still be necessary.

## Future hardened-firmware work (NOT part of this binary)

Research a watchdog with independent main-loop/stack progress and safe reset accounting, then test on a **non-PM spare**, including deliberate parent loss, stalled OTA, watchdog resets and repeated failed boot. Do not pet a watchdog unconditionally from a timer that may continue while Zigbee hangs. Do not write boot-slot validity flags until the exact Telink SDK OTA state machine, flash-protection rules, NVM locations and failure windows have been independently verified. Software boot fallback cannot recover a dead power supply, damaged GPIO/relay circuit or loss of MCU execution before recovery logic runs.
