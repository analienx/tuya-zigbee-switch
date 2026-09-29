# BedroomSocketCabinetRight non-PM `cli5-rc1` — enforced flash-hardening gate

**Status:** Candidate built and OTA packaging verified; `cli4` remains installed. No additional flash or relay operation was performed during this hardening pass. A green read-only `qualify` is **NOT** a flash authorization or hardware acceptance.

The `flash` mode in BOTH `helper_scripts/bseed_ota_campaign.py` and `helper_scripts/bseed_targeted_z2m_ota.py` now runs the private, exact-device `bseed_nonpm_recovery_gate.verify_recovery` **before** opening MQTT, starting a firmware transfer or updating the campaign lock. There is no bypass through the lower-level runner. PM/Router workflows are not subject to this non-PM-only hardware gate.

## Required evidence before a non-PM flash

- Confirm the physical appliance is **unplugged from this specific socket** immediately before that flash; pass the one-shot `--confirm-load-unplugged` flag. A prior MQTT `state_relay: OFF` or a prior link-gate pass cannot confirm the actual appliance or contacts.
- Verify the exact non-PM board, chip marking, and actual flash capacity; do not substitute facts from the distinct failed PM socket. Obtain the correct Telink TLSR8258 SWire programmer and validated low-voltage pinout, power supply and proper disconnection/isolation from mains. An SOIC clip by itself is NOT proof of in-circuit access to this chip.
- With the target board **fully disconnected from mains**, and only with a competent, safely isolated setup, capture the entire flash twice as **two independent read operations**; preserve each original capture as a private file. Record the matching SHA-256 and exact capacity. Verify the documented return-to-service and restore procedure before considering OTA. Do not attach a programmer or clip to a live mains socket PCB.
- Store the real evidence as a PRIVATE JSON file outside the repository, using `docs/bseed_nonpm_recovery_evidence.example.json` as a schema reference. Replace placeholders only with independently established facts, not assumed values; operator truth fields are attestations and cannot be machine-proven by JSON.
- Reference the private evidence file with `recovery_evidence` in the private campaign profile. This gate checks exact IEEE, non-PM board/chip and `cli5-rc1` image SHA, pinned 32-byte transfer blocks, separate complete matching readback files, native Telink boot marker, installed `cli4` build string and board config in the backup. It does not certify that SWire programming can recover every boot failure.

## Deployment sequence, only after the physical recovery gate is genuinely satisfied

Use `--mode qualify` again with the exact private profile while the one-image HTTP server is independently verified and no other Zigbee OTA is active. This command is read-only and expires its own previous check; it performs three separated read-only GETs, targeted OTA eligibility, then three more GETs. It deliberately cannot flash.

The subsequent **separate** single-device command uses `--mode flash --confirm-ieee EXACT_IEEE --confirm-load-unplugged`. Both the campaign and lower-level runner recheck the recovery record and require the normal fresh OTA and link-gate evidence. The one-device transfer is pinned to 32-byte blocks, followed by targeted re-interview, actual `cli5-rc1` build identification and independent relay/settings/network acceptance. Do not run that flash command during a code review or infer physical confirmation from a saved config.

If `recovery_evidence` or either full-flash readback is missing, the new flash gate stops **locally before network access**, leaving the installed `cli4` untouched. The present BedroomSocketCabinetRight private profile has no `recovery_evidence`: this is the expected, intentional blocked state. The private HTTP image server also remains stopped after earlier qualification.

**Limits:** Two equal readback files and operator declarations can be checked by software but do not prove two genuinely separate physical captures, safe mains isolation or successful recovery after a real flash failure. Even a verified programmer cannot eliminate component/hardware failure risk. The separate PM socket incident does not validate this non-PM route. Do not alter `cli5-rc1` firmware bytes, fabricate recovery evidence or bypass the gate to obtain a green status.
